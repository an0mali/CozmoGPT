This project is designed to bring modern AI to Cozmo and give us the Anki Cozmo we all wanted but just wasn't possible.

It gives Cozmo full speech recognition, conversational diaglog, and situational awareness- including the ability to interpret and navigate its surroundings visually using its camera.
Sadly, Cozmo is still tethered to the phone app, and the phone app will need to be running in SDK mode and connected to your sPCystem with android debugging enabled in order to function.

You can craft cozmo's personality by modifying personality_core.personality

It is not recommeded to modify personality_core.perception as this is specifically crafted to return executable commands from cozmo-sdk to allow cozmo to move and act how he "sees" fit.

Requirements are version specific. Will update as I get around to it.

This project uses slightly modified versions of azure_speech_to_text.py and openai_chat.py from DougDoug's Babagaboosh Github project https://github.com/DougDougGithub/Babagaboosh

This project requires Microsoft Azure TTS and OpenAI services, and api keys should be set as environment variables:
AZURE_TTS_KEY, AZURE_TTS_REGION, and OPENAI_API_KEY.




