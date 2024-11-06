from flask import Flask, request, abort
import requests
from pydub import AudioSegment
import whisper
import os
from dotenv import load_dotenv
import warnings
from twilio.rest import Client
import logging
from functools import wraps
from twilio.request_validator import RequestValidator
from flask_cors import CORS

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

# Initialize Whisper model
model = whisper.load_model("large")

# Initialize Twilio client with explicit credentials
twilio_client = Client(
    os.getenv('TWILIO_ACCOUNT_SID'),
    os.getenv('TWILIO_AUTH_TOKEN')
)

warnings.filterwarnings("ignore", category=FutureWarning, module="whisper")

def download_audio(url):
    """Download audio file from URL"""
    try:
        # Add authentication to the request
        auth = (os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN'))
        response = requests.get(url, auth=auth)
        response.raise_for_status()  # Raise an error for bad status codes
        
        logger.debug(f"Download response status: {response.status_code}")
        
        with open("temp_audio.ogg", "wb") as f:
            f.write(response.content)
        
        # Convert OGG to WAV (Whisper works better with WAV)
        audio = AudioSegment.from_ogg("temp_audio.ogg")
        audio.export("temp_audio.wav", format="wav")
        return "temp_audio.wav"
    except Exception as e:
        logger.error(f"Error downloading audio: {str(e)}")
        logger.error(f"Response content: {getattr(response, 'content', 'No content')}")
        raise

def transcribe_audio(file_path):
    """Transcribe audio file using Whisper"""
    # Add language='he' for Hebrew optimization
    result = model.transcribe(
        file_path,
        language='he',  # Specify Hebrew
        task='transcribe'
    )
    return result["text"]

def send_whatsapp_message(message, to_number):
    """Send WhatsApp message using Twilio"""
    from_number = f"whatsapp:{os.getenv('TWILIO_WHATSAPP_NUMBER')}"
    to_number = f"whatsapp:{to_number}"
    
    twilio_client.messages.create(
        body=message,
        from_=from_number,
        to=to_number
    )

def validate_twilio_request(f):
    """Validates that incoming requests genuinely originated from Twilio"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Get the request data
        validator = RequestValidator(os.getenv('TWILIO_AUTH_TOKEN'))
        
        # Validate the request
        twilio_signature = request.headers.get('X-Twilio-Signature', '')
        url = request.url
        form_data = request.form.to_dict()
        
        logger.debug(f"Validating request with signature: {twilio_signature}")
        logger.debug(f"URL: {url}")
        logger.debug(f"Form data: {form_data}")
        
        if not validator.validate(url, form_data, twilio_signature):
            logger.error("Invalid Twilio request")
            abort(403)
        
        return f(*args, **kwargs)
    return decorated_function

@app.route("/", methods=["GET"])
def home():
    return "Welcome to the WhatsApp Transcriber!"

@app.route("/favicon.ico", methods=["GET"])
def favicon():
    return "", 204

@app.route("/webhook", methods=["POST"])
# @validate_twilio_request  # Comment out this line temporarily
def webhook():
    """Handle incoming WhatsApp messages"""
    try:
        # Log all request details
        logger.debug("=== Webhook Request Details ===")
        logger.debug(f"Headers: {dict(request.headers)}")
        logger.debug(f"Values: {dict(request.values)}")
        logger.debug(f"Form: {dict(request.form)}")
        logger.debug(f"Args: {dict(request.args)}")
        logger.debug("============================")
        
        # Get the message details
        incoming_msg = request.values.get('Body', '')
        sender = request.values.get('From', '').split(':')[1]
        num_media = int(request.values.get('NumMedia', 0))
        
        logger.info(f"Processing message from {sender} with {num_media} media items")
        
        # Check if message contains audio
        if num_media > 0:
            media_type = request.values.get('MediaContentType0', '')
            
            if 'audio' in media_type:
                media_url = request.values.get('MediaUrl0', '')
                logger.info(f"Processing audio from URL: {media_url}")
                
                try:
                    audio_file = download_audio(media_url)
                    transcription = transcribe_audio(audio_file)
                    
                    # Send transcription back via WhatsApp
                    from_number = f"whatsapp:{os.getenv('TWILIO_WHATSAPP_NUMBER')}"
                    to_number = f"whatsapp:{sender}"
                    
                    message = twilio_client.messages.create(
                        body=transcription,
                        from_=from_number,
                        to=to_number
                    )
                    
                    logger.info(f"Sent transcription message: {message.sid}")
                    
                    # Clean up
                    os.remove("temp_audio.ogg")
                    os.remove("temp_audio.wav")
                    
                except Exception as e:
                    logger.error(f"Error processing audio: {str(e)}")
                    return str(e), 500
        
        return "OK", 200
        
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return str(e), 500

@app.route("/status", methods=["POST"])
def message_status():
    """Handle message status updates from Twilio"""
    try:
        message_sid = request.values.get('MessageSid', '')
        message_status = request.values.get('MessageStatus', '')
        logger.info(f"Message {message_sid} status: {message_status}")
        return "OK", 200
    except Exception as e:
        logger.error(f"Status callback error: {str(e)}")
        return str(e), 500

@app.route("/test", methods=["GET", "POST"])
def test():
    logger.debug("Test endpoint hit")
    return "Test endpoint working!", 200

if __name__ == "__main__":
    # Verify environment variables are set
    required_vars = ['TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_WHATSAPP_NUMBER']
    for var in required_vars:
        if not os.getenv(var):
            raise ValueError(f"Missing required environment variable: {var}")
    
    # Explicitly define host and port
    app.run()
