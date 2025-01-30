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
        self.robot = None

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
        self.is_idle = True
        self.first_explore = True # used to Take a picture on first explore

        self.conversation_mode = False #enable conversation mode
        #Issue with releasing built-in mic on laptop

        self.explore_mode = True #disable explore mode, may cause errors if both turned on at same time?

        ###Cozmo chaning variables ### Weird workarounds for cozmo function calls
        self.speech = "TEST ALL THE THINGS"#this can be deleted, no longer used

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
        #init stream, maybe do this only when listening?
        self.stream = pa.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=self.CHUNK)
        self.speechtotext_manager = SpeechToTextManager()
        ###

        ###BACKUP###
        self.BACKUP_FILE = "ChatHistoryBackup.txt"
        
        #asyncio.run(self.main())
       # cozmo.run_program(self.cozmo_capture_image)
        #start no response timer so he wil explore if not spoken to
        self.allow_response_timer.start()
        #self.thread = threading.Thread(target=self.cozmo_converse)
       # cozmo.run_program(self.set_initial_head_angle)

        #self.thread = threading.Thread(target=self.main)
        #self.thread.start()
            #self.cozmo_converse()
            #asyncio.run(self.explore())

        #self.main()

    def cozmo_main(self, robot: cozmo.robot.Robot):
        #maybe rename to cozmo init
        
        #set robot in self for easy reference
        self.robot = robot

        #set default head and lift position
        self.set_initial_pose()

        #capture the initial image of the environment
        self.cozmo_capture_image()
        print("Cozmo main is init")

        #start main thread
        self.main()

    def set_initial_pose(self):
        self.robot.set_head_angle(degrees(15)).wait_for_completed()
        self.robot.set_lift_height(0.0).wait_for_completed()

    def main(self):
        #not really used tbh
        #Main function so we can use asyncio on stuff
        #Stuff is currently test only
        if self.conversation_mode:
            self.thread = threading.Thread(target=self.cozmo_converse)
            self.thread.start()
        if self.explore_mode:
            while True:
                self.explore()
                time.sleep(1)
        #    self.explore()
        #    time.sleep(1)

    def explore(self):
        print("Starting to explore")
        
        b64_image = self.get_b64_image()
        openai_result = self.openai_manager.chat_with_history(self.sight_core, b64_image)
        speech = self.parse_gpt_response(openai_result)
        self.cozmo_say(speech) #have cozmo say it)
        self.cozmo_actions()

    def cozmo_converse(self):
        #Primary chatgpt audio interface function or something

        ###Listen on mic for noise, if noise turn on STT and return result
        while True:
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
                        self.allow_cozmo_response = True
                    self.allow_response_timer.cancel()
                    self.allow_response_timer = threading.Timer(60.0, self.set_allow_response_false)
                    self.allow_response_timer.start()

    def set_allow_response_false(self):
        self.allow_cozmo_response = False
        self.is_idle = True
        #Explroe until cozmo is spoken to again
        while self.is_idle:
            self.explore()
            time.sleep(1)

    def cozmo_actions(self):
        #For ququed action execution?
        
        #Capture image
        
        self.execute_cozmo_actions()
        self.cozmo_capture_image()
    
    def parse_gpt_response(self,text):
        #Parse prompt to remove sentiment and then set sentiment animation trigger
        #Also begins execution of actions extracted from response
        actions = False

        #Cozmo prompt is inconsistent in obeying "no space after ;;" rule, so we need to account for that
        #small changes to the prompt are causing large changes in result
        parsedtext = ''
        if ";; " in text:
            parsedtext = text.split(";; ")
        else:
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
            #
            actions_array = ast.literal_eval(self.actions)
            #for each item in array, we need to call a command_run on cozmo
            for action in actions_array:
                if not 'robot' in action:
                    action = 'robot.' + action
                self.execute_action(action)
                #cozmo.run_program(self.execute_action)
            self.actions = False

    def execute_action(self, action):
        robot = self.robot
        #Should call cozmo to run the string format action command
        #This is dangerous as-is (but fun!), need sanitizers
        try:
            eval(action)
        except Exception as e:
            print(f"Error executing cozmo body code: {e}")


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

    def cozmo_capture_image(self):
        robot = self.robot

        #black and white seems to work better, removing cozmos face screen might help get clearer picture?
        robot.camera.color_image_enabled = False
        robot.world.add_event_handler(cozmo.world.EvtNewCameraImage, self.on_new_camera_image)
        robot.camera.image_stream_enabled = True
        robot.world.wait_for(cozmo.world.EvtNewCameraImage)
        robot.camera.image_stream_enabled = False
        time.sleep(0.1)#give time for image to save

    def process_cozmotss_string(self, text):
        #Take string and split it into 255 char chunks, dividing at punctuation to avoid cutting off mid sentence
        words = text.split()
        new_string = ""
        char_count = 0

        for word in words:
            if char_count + len(word) + len(new_string.split()) > 254:  # Adding space, testing 254 instead of 255
                break
            new_string += word + " "
            char_count += len(word)
            #Split string on punctioation to avoid cutting off mid sentence
            if "." in word or "?" in word or "!" in word:
                break

        new_string = new_string.strip()
        remaining_string = text[len(new_string):].lstrip()

        return new_string, remaining_string

    def cozmo_say(self, text="POGGIES"):

        #Process long response into smaller chunks cozmo can handle by splitting into multiple voice commands
        while len(text) > 255:
            text, remaining_text = self.process_cozmotss_string(text)
            self.robot.say_text(text,use_cozmo_voice=self.cozmo_voice, voice_pitch=self.voice_pitch, duration_scalar=(1.0/(self.speech_rate / 100.0))).wait_for_completed()
            text = remaining_text

        #we need to do for eacch 255 chars of string, say_text, and iterate until entire string is spoke to get around say_text 255 char limit
        self.robot.say_text(text,use_cozmo_voice=self.cozmo_voice, voice_pitch=self.voice_pitch, duration_scalar=(1.0/(self.speech_rate / 100.0))).wait_for_completed()
        #cozmo.run_program(self.cozmo_tts)

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

#Can we create object, then pass the main function into cozmo_run_program? This wouild allow robot object to be referenced in self
cozmo.run_program(cmo.cozmo_main, use_3d_viewer=True) #yes, yes we can. Duh