import time
import cozmo
import os
from rich import print
from azure_speech_to_text import SpeechToTextManager
from openai_chat import OpenAiManager
#import asyncio
import pyaudio #move this to new module?
import numpy as np
import threading
from PIL import Image
import base64

import logging
import ast
import personality_core
import cozmo_ctrl

# Set the logging level to WARNING to reduce verbosity

#Contains Cozmo's functions and controls

####TO DO:
### Wipe cozmo move actions from text prompt before adding to chat history
### Figure out logic for simulataneous exploration and conversation

class CozmoGpt(object):

    def __init__(self, name):

        self.name = name
        #we can use if not self.robot for non-cozmo debug
        self.robot = False 
        #Must be initialized after self.robot is set
        self.cozmo_ctrl = None 

        pcore = personality_core.Personality_Core()
        ###Cozmo personality vars###
        self.speech_rate = 250
        self.cozmo_voice = True
        self.voice_pitch = -2.0
        ###Load up personality file so they are swappable
        self.personality_core = pcore.personality
        ###Load up sight prompt
        self.sight_core = pcore.perception
        #Set prompt for when we send GPT new images
        self.sight_prompt = pcore.sight_prompt
        self.speech_enabled = False #used for debugging

        #########################
        ###Cozmo control vars####
        #########################

        #Determines is cozmo listens and responds conversationally or if he ignores input that doesn't reference him
        self.allow_cozmo_response = False
        #create a timer that gives cozmo some time after a recent message to determine if he should respond conversationally
        self.allow_response_timer = threading.Timer(30.0, self.set_allow_response_false)
        #A variable that controls whether cozmo is self feeding new images after movements in prompts in order to learn and interact with the environment
        self.cozmo_explore = False
        self.actions = False
        self.is_idle = True
        # Just keeps track of if this is first explorer mode cycle, if so, we take a picture for cozmo's prompt
        self.first_explore = True 
         #enable/disable conversation mode
        self.conversation_mode = False
        #Issue with releasing built-in mic on laptop

        #disable/enable explore mode, mainly for testing, goal is to have both conversation mode and explore mode run simulataneously
        self.explore_mode = True 

        #############
        ###ChatGPT###
        #############
        #Instance chatgpt object, only needs to run once
        self.openai_manager = openai_manager = OpenAiManager()

        #Set personality message
        initial_prompt = self.personality_core
        if self.explore_mode:
            initial_prompt += ' ' + self.sight_core
        FIRST_SYSTEM_MESSAGE = {"role": "system", "content": initial_prompt}
        openai_manager.chat_history.append(FIRST_SYSTEM_MESSAGE)

        ##################################
        ### Microphone input detection ###
        ##################################
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
        
        ###Chat BACKUP###
        self.BACKUP_FILE = "ChatHistoryBackup.txt"
        

        #start no response timer so he wil explore if not spoken to
        self.allow_response_timer.start()

    def cozmo_main(self, robot: cozmo.robot.Robot):
        ### This function passes into cozmo.run_program to get robot object, set in self, and run main thread
        

        #maybe rename to cozmo init
        
        #set robot in self for easy reference
        self.robot = robot
        #initalize control module
        self.cozmo_ctrl = cozmo_ctrl.Cozmo_Ctrl(self.robot)

        #set default head and lift position
        self.cozmo_ctrl.set_initial_pose()

        #Add event handler for images, we only want to do this once.
        self.robot.world.add_event_handler(cozmo.world.EvtNewCameraImage, self.on_new_camera_image)
        
        print("Cozmo main is init")

        #start main thread
        self.main()

    def main(self):

        
        #Run convo mode, explore mode or both
        if self.conversation_mode:
            self.thread = threading.Thread(target=self.cozmo_converse)
            self.thread.start()
        if self.explore_mode:
            while True:
                self.explore()
                time.sleep(1)

    def get_cam_image(self):
        self.cozmo_capture_image()
        b64_image = self.get_b64_image()
        return b64_image

    def explore(self):
        #Cozmo takes pictures of environment to understand it and explores/discusses on his own
        print("Starting to explore")

        
        #Get image from cam
        b64_image = self.get_cam_image()

        collisison_prompt = self.cozmo_ctrl.get_collision_prompt()
        move_history_prompt = self.cozmo_ctrl.get_movement_prompt()

        explore_prompt = self.sight_prompt + collisison_prompt + move_history_prompt

        print("Explore prompt: " + explore_prompt)

        #We should add self.sight_core to first system message instead of adding it here.
        #Maybe just add "this image is what you currently see"?
        openai_result = self.openai_manager.chat_with_history(explore_prompt, b64_image)
        speech = self.parse_gpt_response(openai_result)
        #have cozmo say it
        self.cozmo_say(speech)
        #cozmo performs movement actions based on prompt response
        self.cozmo_actions()

    def cozmo_converse(self):
        #Primary chatgpt audio interface function or something

        ###Listen on mic for noise, if noise turn on STT and return result
        while True:
                print("Listening for STT")
                #listens to mic until volume thresh is above THRESHOLD
                self.listen_for_mic_input()
                mic_result = self.speechtotext_manager.speechtotext_from_mic_continuous(stream=self.stream)
                if mic_result == '':
                    print("[red]Did not receive any input from your microphone!")
                    continue
                
                #Check if cozmo is spoken to
                cozmo_mentioned = self.check_cozmo_mentions(mic_result)
                if cozmo_mentioned:
                    #Set is idle to false so cozmo doesn't perform explore function
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
        
    
    def parse_gpt_response(self,text):
        #Parse prompt to remove sentiment and then set sentiment animation trigger
        #Also begins execution of actions extracted from response
        actions = False

        #Cozmo prompt is inconsistent in obeying "no space after ;;" rule, so we need to account for that
        #small changes to the prompt are causing large changes in result
        parsedtext = ''
        #if ";; " in text:
        parsedtext = text.split(";; ")
        parsedtext = parsedtext[0].split(";;")

        #used in old implemenation
        #if len(parsedtext) > 1:
        #    emotion = parsedtext[1].replace(" ", "")#remove any spaces in prompt
        speech = parsedtext[0]
        if len(parsedtext) > 1:
            if parsedtext[1] != []:
                actions = str(parsedtext[1])
                actions = actions.replace("cozmo.robot.Robot", "robot")
        
        if actions:
            self.actions = actions
        
        return speech

    def execute_cozmo_actions(self):
        if self.actions:
            #Enable env reactions allows cozmo to better detect ledges and collisions, but we need to pass this into prompt
            #Enable environmental reactions, this may cause bugs in movement
            self.robot.enable_all_reaction_triggers(True) 
        #We take the self.actions, which is an array in the format of a string, and convert it to array
            try:
                print("Actions: '" + self.actions + "'")
                actions_array = ast.literal_eval(self.actions)
                for action in actions_array:
                    #Actions array should be ["action string", units]
                    act = action[0]
                    unit = action[1]
                    self.cozmo_ctrl.perform_action(act, unit)
            except Exception as e:
                print("ERROR: execute_cozmo_actions: " + str(e))
            #Disable env reactionsc
            self.robot.enable_all_reaction_triggers(False)

            self.actions = False

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
       
        robot.camera.image_stream_enabled = True
        robot.world.wait_for(cozmo.world.EvtNewCameraImage)
        robot.camera.image_stream_enabled = False
        time.sleep(1)#give time for image to save

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
        if not self.speech_enabled:
            return
        #Process long response into smaller chunks cozmo can handle by splitting into multiple voice commands
        while len(text) > 255:
            text, remaining_text = self.process_cozmotss_string(text)
            self.cozmo_ctrl
            self.robot.say_text(text,use_cozmo_voice=self.cozmo_voice, voice_pitch=self.voice_pitch, duration_scalar=(1.0/(self.speech_rate / 100.0))).wait_for_completed()
            text = remaining_text

        #we need to do for eacch 255 chars of string, say_text, and iterate until entire string is spoke to get around say_text 255 char limit
        self.robot.say_text(text,use_cozmo_voice=self.cozmo_voice, voice_pitch=self.voice_pitch, duration_scalar=(1.0/(self.speech_rate / 100.0))).wait_for_completed()
        #cozmo.run_program(self.cozmo_tts)

    def set_allow_response_false(self):
        self.allow_cozmo_response = False

    def load_prompt_core(self, core="personality"):
        #Load personality core file, a prompt that defines strict rules on how to answer questions
        #Deprecated, use personality_core.py instead
        personality = ""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        personality_path = os.path.join(script_dir, core + ".core")
        with open(personality_path, 'r') as file:
            personality = file.read()
        return personality

cmo = CozmoGpt("Cozmo")

#Can we create object, then pass the main function into cozmo_run_program? This wouild allow robot object to be referenced in self
cozmo.run_program(cmo.cozmo_main, use_3d_viewer=True) #yes, yes we can. Duh