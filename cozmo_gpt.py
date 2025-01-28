import time
import cozmo
import os
from rich import print
from azure_speech_to_text import SpeechToTextManager
from openai_chat import OpenAiManager
import asyncio
import pyaudio #move this to new module?
import numpy as np
import threading
from PIL import Image
import base64
from cozmo.util import degrees
from cozmo.util import distance_mm, speed_mmps
import logging
import ast
import personality_core

# Set the logging level to WARNING to reduce verbosity

#Contains Cozmo's functions and controls

class CozmoGpt(object):

    def __init__(self, name):

        self.name = name
        self.robot = cozmo.robot.Robot

        pcore = personality_core.Personality_Core()
        ###Cozmo personality vars###
        self.speech_rate = 150
        self.cozmo_voice = True
        self.voice_pitch = -1.0
        ###Load up personality file so they are swappable
        self.personality_core = pcore.personality
        ###Load up sight prompt
        self.sight_core = pcore.perception

        ###Cozmo control vars####

        #Determines is cozmo listens and responds conversationally or if he ignores input that doesn't reference him
        self.allow_cozmo_response = False
        #create a timer that gives cozmo some time after a recent message to determine if he should respond conversationally
        self.allow_response_timer = threading.Timer(30.0, self.set_allow_response_false)
        #A variable that controls whether cozmo is self feeding new images after movements in prompts in order to learn and interact with the environment
        self.cozmo_explore = False
        self.actions = False
        self.exec_action = ""
        self.is_idle = True

        ###Cozmo chaning variables ### Weird workarounds for cozmo function calls
        self.speech = "TEST ALL THE THINGS"

        ###ChatGPT###

        #Instance chatgpt object, only needs to run once
        self.openai_manager = openai_manager = OpenAiManager()
        #Set personality message
        FIRST_SYSTEM_MESSAGE = {"role": "system", "content": self.personality_core}
        openai_manager.chat_history.append(FIRST_SYSTEM_MESSAGE)
        ###

        ### Microphone input detection ###
        self.THRESHOLD = 58
        pa = pyaudio.PyAudio()
        #stream args
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 44100
        self.CHUNK = 1024
        #init stream
        self.stream = pa.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=self.CHUNK)
        self.speechtotext_manager = SpeechToTextManager()
        ###

        ###BACKUP###
        self.BACKUP_FILE = "ChatHistoryBackup.txt"
        
        #asyncio.run(self.main())
        cozmo.run_program(self.cozmo_capture_image)
        #start no response timer so he wil explore if not spoken to
        self.allow_response_timer.start()
        #self.thread = threading.Thread(target=self.cozmo_converse)
        cozmo.run_program(self.set_initial_head_angle)
        self.thread = threading.Thread(target=self.main)
        try:
            self.thread.start()
            #self.cozmo_converse()
        except KeyboardInterrupt:
            self.thread.stop()
            #asyncio.run(self.explore())

    def set_initial_head_angle(self, robot: cozmo.robot.Robot):
        robot.set_head_angle(degrees(15)).wait_for_completed()

    def main(self):
        #not really used tbh
        #Main function so we can use asyncio on stuff
        #Stuff is currently test only

        #task = asyncio.create_task(self.cozmo_converse())
        while True:
            self.explore()
        #thead = threading.Thread(target=asyncio.run(self.cozmo_converse()))
        #asyncio.run(self.explore())

    def explore(self):
        print("Starting to explore")
        cozmo.run_program(self.cozmo_capture_image)
        b64_image = self.get_b64_image()
        openai_result = self.openai_manager.chat_with_history(self.sight_core, b64_image)
        speech = self.parse_gpt_response(openai_result)
        self.cozmo_say(speech) #have cozmo say it)
        self.cozmo_actions()

    def cozmo_converse(self):
        #Primary chatgpt audio interface function or something

        ###Listen on mic for noise, if noise turn on STT and return result
        while True:
            try:
                print("Listening for STT")
                #asyncio.run(self.listen_for_mic_input())
                self.listen_for_mic_input()#listens to mic until volume thresh is above THRESHOLD
                mic_result = self.speechtotext_manager.speechtotext_from_mic_continuous(stream=self.stream)
                if mic_result == '':
                    print("[red]Did not receive any input from your microphone!")
                    continue

                cozmo_mentioned = self.check_cozmo_mentions(mic_result)
                if cozmo_mentioned:
                    #Set is idle to false so cozmo stops and talks
                    self.is_idle = False
                    #We're going to genrate a prompt for GPT, so grab the recent cozmo image
                    b64_image = self.get_b64_image()
                    #Add sight instruction to prompt
                    #might be better to add to FIRST_SYSTEM_MESSAGE?
                    mic_result += self.sight_core
                    openai_result = self.openai_manager.chat_with_history(mic_result, b64_image) #Get GPT reult
                    #parse cozmo's response to pull and execute control functions and remove those so theyre not spoken
                    speech = self.parse_gpt_response(openai_result)
                    
                    self.cozmo_say(speech) #have cozmo say it)
                    self.cozmo_actions()
                    
                    #Give a 60 second time window in which cozmo's name doesnt have to be in prompt for him to respond
                    if not self.allow_cozmo_response:
                        allow_cozmo_response = True
                    self.allow_response_timer.cancel()
                    self.allow_response_timer = threading.Timer(60.0, self.set_allow_response_false)
                    self.allow_response_timer.start()
            except KeyboardInterrupt:
                break

    def set_allow_response_false(self):
        self.allow_cozmo_response = False
        self.is_idle = True
        asyncio.run(self.explore())

    def cozmo_actions(self):
        #For ququed action execution?
        
        #Capture image
        cozmo.run_program(self.cozmo_capture_image)
        self.execute_cozmo_actions()
    
    def parse_gpt_response(self,text):
        #Parse prompt to remove sentiment and then set sentiment animation trigger
        #Also begins execution of actions extracted from response
        actions = False
        parsedtext = text.split(";;")

        #used in old implemenation
        #if len(parsedtext) > 1:
        #    emotion = parsedtext[1].replace(" ", "")#remove any spaces in prompt
        speech = parsedtext[0]
        if len(parsedtext) > 1:
            if parsedtext[1] != "":
                actions = parsedtext[1]
                actions = actions.replace("cozmo.robot.Robot", "robot")
        
        if actions:
            self.actions = actions
        
        return speech

    def execute_cozmo_actions(self):
        if self.actions:
        #We take the self.actions, which is an array in the format of a string, and convert it to array
            actions_array = ast.literal_eval(self.actions)
            #for each item in array, we need to call a command_run on cozmo
            for action in actions_array:
                self.exec_action = action
                cozmo.run_program(self.execute_action)
            self.actions = False

    def execute_action(self, robot: cozmo.robot.Robot):
        #Should call cozmo to run the string format action command
        eval(self.exec_action)


    def bkup_history(self):
        try:
            with open(self.BACKUP_FILE, "w") as file:
                file.write(str(self.openai_manager.chat_history))
        except UnicodeEncodeError as e:
            print("Couldnt save chat log due to unicode error")


    def get_b64_image(self):
        def encode_image(image_path):
            #used only by 
            with open(image_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode('utf-8')
            
        #Use to get encoded version of image to attach to ChatGPT prompt
        image_path = "cozmo_image.png"
        b64_image = encode_image(image_path)
        return b64_image
    


    def check_cozmo_mentions(self, text):
        #Parse STT mic input and return true or false if cozmo is mentioned
        found_keyword = False
        for cozname in ["cosmo", "osmo", "cozmo", "ozmo", "you", "your"]:
            if cozname in text.lower():
                found_keyword = True

        if (not found_keyword) and (not self.allow_cozmo_response):
            print("Not speaking to cozmo, continuing")
            return False
        return True

    def listen_for_mic_input(self):
        while True:
            data = self.stream.read(self.CHUNK)
            audio_data = np.frombuffer(data, dtype=np.int16)
        # print(audio_data)
            rms_amplitutde = np.sqrt(abs(np.mean(audio_data**2)))
            #print("RMS Amplitude: " + str(rms_amplitutde))
            if rms_amplitutde > self.THRESHOLD:
                break
            time.sleep(0.01)
        print('Mic input detected')

    async def on_new_camera_image(self, evt, **kwargs):
        #get camera image from cozmo for use in determining surroundings
        # Get the latest image
        image = evt.image
        # Convert the image to a PIL image
        pil_image = image.raw_image
        # Save the image to a file
        pil_image.save("cozmo_image.png")
        print("Image captured and saved as cozmo_image.png")

    def cozmo_capture_image(self, robot: cozmo.robot.Robot):
       
        robot.camera.color_image_enabled = True
        robot.world.add_event_handler(cozmo.world.EvtNewCameraImage, self.on_new_camera_image)
        robot.camera.image_stream_enabled = True
        robot.world.wait_for(cozmo.world.EvtNewCameraImage)
        robot.camera.image_stream_enabled = False

    async def cozmo_view(self):
        print("Cozmo view go!")
        task = asyncio.create_task(cozmo.run_program(self.cozmo_keepalive, use_3d_viewer=True))
        print("Cozmo view async")

    def cozmo_keepalive(self, robot):
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    def cozmo_say(self, text="POGGIES"):
        self.speech = text
        cozmo.run_program(self.cozmo_tts)

    def cozmo_tts(self, robot: cozmo.robot.Robot):
        #Seems cozmo.run_program passes robot object in as first positional arg?
        #Probably not a good idea to async and this is an action cozmo can't simulataneously

        #It doesnt seem we can pass args to these run_program calls. So we use self instead!
        robot.say_text(self.speech,use_cozmo_voice=self.cozmo_voice, voice_pitch=self.voice_pitch, duration_scalar=(1.0/(self.speech_rate / 100.0))).wait_for_completed()
        
    def set_allow_response_false(self):
        self.allow_cozmo_response = False

    def load_prompt_core(self, core="personality"):
        #Load personality core file, a prompt that defines strict rules on how to answer questions
        personality = ""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        personality_path = os.path.join(script_dir, core + ".core")
        with open(personality_path, 'r') as file:
            personality = file.read()
        return personality

cmo = CozmoGpt("Cozmo")