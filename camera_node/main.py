import socket
import struct
import time
import json
import os
import threading
import argparse
import paho.mqtt.client as mqtt
import cv2
import numpy as np
from picamera2 import Picamera2
from queue import Queue
import glob

import sys
base_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(base_dir, 'src'))
# Pre-processing modules removed for Pi Zero 2W performance

# --- Configuration Loader ---
parser = argparse.ArgumentParser(description="Camera Sender Script")
parser.add_argument('-c', '--config', type=str, default=os.path.join(base_dir, 'configs', 'config.json'), help='Path to config file')
parser.add_argument('--mock_dir', type=str, default=None, help='Directory containing mock images for offline testing')
parser.add_argument('--debug_align', action='store_true', help='Save visualization of the alignment process to disk')
parser.add_argument('--disable_clahe', action='store_true', help='Disable CLAHE enhancement and use raw grayscale')
args = parser.parse_args()

try:
    with open(args.config, 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    print(f"CRITICAL: {args.config} not found. Generating fallback configuration for initial calibration!")
    config = {
        "tcp": {
            "ip": "10.10.10.199",
            "port": 8080 if "cam0" in args.config else 8081
        },
        "mqtt": {
            "broker": "wfmain.local",
            "port": 1883,
            "topic_cmd": f"{socket.gethostname()}/w/command",
            "topic_status": f"{socket.gethostname()}/w/status"
        },
        "camera": {
            "id": 0 if "cam0" in args.config else 1,
            "default_width": 2304,
            "default_height": 1296,
            "jpeg_quality": 90,
            "continuous_stream": False,
            "stream_interval": 1.0,
            "loop_delay": 0.05
        },
        "preprocessing": {
            "enable_alignment": False,
            "enable_shadow_removal": False,
            "enable_pre_crop": False,
            "enable_grayscale": False,
            "enable_clahe": False,
            "enable_box_cropping": False
        }
    }
    # Auto-save the fallback config so it exists for next time
    os.makedirs(os.path.dirname(os.path.abspath(args.config)), exist_ok=True)
    try:
        with open(args.config, 'w') as f:
            json.dump(config, f, indent=4)
        print(f"INFO: Saved fallback configuration to {args.config}")
    except Exception as e:
        print(f"ERROR: Failed to save fallback config: {e}")

TCP_IP = config.get("tcp", {}).get("ip", "10.10.10.199")
TCP_PORT = config.get("tcp", {}).get("port", 8080)
MQTT_BROKER = config.get("mqtt", {}).get("broker", "10.10.10.199")
MQTT_PORT = config.get("mqtt", {}).get("port", 1883)
MQTT_TOPIC_CMD = config.get("mqtt", {}).get("topic_cmd", "camera/command")
MQTT_TOPIC_STATUS = config.get("mqtt", {}).get("topic_status", "camera/status")

hostname = socket.gethostname()
# Replace hardcoded wf52 prefix with actual hostname to prevent conflicts on new boards
if MQTT_TOPIC_CMD.startswith("wf52/"):
    MQTT_TOPIC_CMD = f"{hostname}/" + MQTT_TOPIC_CMD.split("/", 1)[1]
elif "{hostname}" in MQTT_TOPIC_CMD:
    MQTT_TOPIC_CMD = MQTT_TOPIC_CMD.replace("{hostname}", hostname)

# Force status topic to be <hostname>/status as requested
MQTT_TOPIC_STATUS = f"{hostname}/status"

MQTT_USERNAME = config.get("mqtt", {}).get("username", None)
MQTT_PASSWORD = config.get("mqtt", {}).get("password", None)

CAMERA_ID = config.get("camera", {}).get("id", 0)
JPEG_QUALITY = config.get("camera", {}).get("jpeg_quality", 90)
CONTINUOUS_STREAM = config.get("camera", {}).get("continuous_stream", True)
STREAM_INTERVAL = config.get("camera", {}).get("stream_interval", 1.0)
LOOP_DELAY = config.get("camera", {}).get("loop_delay", 0.05)

# Default camera config
current_width = config.get("camera", {}).get("default_width", 2304)
current_height = config.get("camera", {}).get("default_height", 1296)

# Global state
picam2 = None
tcp_socket = None
capture_triggered = False
capture_lock = threading.Lock()
image_queue = Queue(maxsize=7) # Limit queue size to avoid OOM
last_mock_image_name = None # Track the current mock image name

# Raw Image Sending Mode Configuration
# (Pre-processing disabled for Pi Zero 2W performance)

class MockCamera:
    def __init__(self, image_dir):
        self.images = glob.glob(os.path.join(image_dir, '*.jpg'))
        self.images.sort() # Ensure consistent order
        self.idx = 0
        if not self.images:
            raise ValueError(f"No mock images found in {image_dir}")
        print(f"INFO: Initialized MockCamera with {len(self.images)} images from {image_dir}")

    def capture_array(self):
        global last_mock_image_name
        img_path = self.images[self.idx]
        last_mock_image_name = os.path.basename(img_path)
        frame = cv2.imread(img_path)
        if frame is None:
             raise RuntimeError(f"Failed to read mock image: {img_path}")
        self.idx = (self.idx + 1) % len(self.images)
        return frame
        
    def start(self): pass
    def stop(self): pass
    def configure(self, config): pass
    def create_preview_configuration(self, main): return {}
    def set_controls(self, controls): pass

# init_preprocessing removed for raw image sending

def get_cpu_temperature():
    """Reads CPU temperature from system files."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp = float(f.read().strip()) / 1000.0
        return temp
    except Exception:
        return 0.0

def save_config():
    """Saves the current config dictionary back to the JSON file."""
    try:
        with open(args.config, 'w') as f:
            json.dump(config, f, indent=4)
        print(f"INFO: Saved updated configuration to {args.config}")
    except Exception as e:
        print(f"ERROR: Failed to save config to {args.config}: {e}")

last_cpu_idle = 0
last_cpu_total = 0

def get_cpu_usage():
    """Calculates CPU usage percentage from /proc/stat."""
    global last_cpu_idle, last_cpu_total
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        
        if not line.startswith('cpu '):
            return 0.0
            
        parts = [float(p) for p in line.split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        non_idle = parts[0] + parts[1] + parts[2] + (sum(parts[5:8]) if len(parts) > 7 else 0)
        total = idle + non_idle
        
        total_diff = total - last_cpu_total
        idle_diff = idle - last_cpu_idle
        
        last_cpu_total = total
        last_cpu_idle = idle
        
        # Return 0.0 on the very first call since delta is not meaningful yet
        if total == total_diff: 
            return 0.0
            
        if total_diff > 0:
            return (total_diff - idle_diff) / total_diff * 100.0
        return 0.0
    except Exception:
        return 0.0

def get_ram_usage():
    """Calculates RAM usage percentage from /proc/meminfo."""
    try:
        with open('/proc/meminfo', 'r') as mem:
            mem_info = mem.readlines()
        
        mem_total = 0
        mem_free = 0
        mem_buffers = 0
        mem_cached = 0
        
        for line in mem_info:
            if line.startswith('MemTotal:'):
                mem_total = int(line.split()[1])
            elif line.startswith('MemFree:'):
                mem_free = int(line.split()[1])
            elif line.startswith('Buffers:'):
                mem_buffers = int(line.split()[1])
            elif line.startswith('Cached:'):
                mem_cached = int(line.split()[1])
                
        used_memory = mem_total - mem_free - mem_buffers - mem_cached
        if mem_total > 0:
            return (used_memory / mem_total) * 100.0
        return 0.0
    except Exception:
        return 0.0

def connect_tcp():
    global tcp_socket
    if tcp_socket:
        try:
            tcp_socket.close()
        except:
            pass
    try:
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_socket.settimeout(5.0) # Handle timeouts gracefully
        tcp_socket.connect((TCP_IP, TCP_PORT))
        print(f"INFO: Connected to TCP server at {TCP_IP}:{TCP_PORT}")
        return True
    except socket.timeout:
        print("ERROR: TCP Connection timed out.")
        tcp_socket = None
        return False
    except ConnectionRefusedError:
        print(f"ERROR: TCP Connection refused by {TCP_IP}:{TCP_PORT}.")
        tcp_socket = None
        return False
    except Exception as e:
        print(f"ERROR: TCP Connection failed: {e}")
        tcp_socket = None
        return False

def send_image(frame, image_id="raw_image"):
    global tcp_socket
    if tcp_socket is None:
        if not connect_tcp():
            return

    try:
        # 1. Encode to JPEG
        # Using a balanced quality to save memory on Zero 2W and reduce transmission time
        result, encoded_frame = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not result:
            print("ERROR: Failed to encode image")
            return

        data = encoded_frame.tobytes()
        img_size = len(data)
        
        # 2. Create JSON Metadata Header
        metadata = {
            "id": image_id,
            "size": img_size
        }
        metadata_json = json.dumps(metadata).encode('utf-8')
        meta_size = len(metadata_json)
        
        # 3. Protocol: 
        # [4-byte Metadata Size] + [JSON Metadata] + [JPEG Data]
        # Receiver reads 4 bytes -> gets meta_size -> reads meta_size bytes for JSON -> parses JSON to get img_size -> reads img_size bytes for Image.
        header = struct.pack(">L", meta_size)
        
        # Send all parts as a single continuous stream
        tcp_socket.sendall(header + metadata_json + data)
        print(f"INFO: Sent {image_id}: {current_width}x{current_height} ({img_size} bytes)")
        
    except (ConnectionResetError, BrokenPipeError, socket.timeout) as e:
        print(f"ERROR: TCP Send Error: {e}")
        tcp_socket.close()
        tcp_socket = None
    except Exception as e:
        print(f"ERROR: Unexpected error during send: {e}")

def image_sender_worker():
    """ Worker thread to process raw images from the queue and send them directly. """
    print("INFO: Image sender worker thread started.")
    while True:
        try:
            frame = image_queue.get()
            
            # In raw mode for Pi Zero 2W, we just send the image directly
            # without running any heavy OpenCV pre-processing algorithms.
            send_image(frame, image_id="raw_image")
            
            image_queue.task_done()
        except Exception as e:
            print(f"CRITICAL ERROR: Unexpected Image Sender worker failure: {e}")
            os._exit(1) # Force exit immediately if there is a fatal error in the thread

def on_mqtt_connect(client, userdata, flags, rc):
    print(f"INFO: Connected to MQTT broker with result code {rc}")
    client.subscribe(MQTT_TOPIC_CMD)
    print(f"INFO: Subscribed to MQTT topic: {MQTT_TOPIC_CMD}")

def on_mqtt_message(client, userdata, msg):
    global capture_triggered, current_width, current_height, picam2, config
    try:
        payload = json.loads(msg.payload.decode())
        print(f"MQTT CMD Received: {payload}")

        if not picam2:
            return

        controls = {}
        config_updated = False
        if "camera_params" not in config:
            config["camera_params"] = {}
            
        # Map JSON payload to Picamera2 controls
        if 'ExposureTime' in payload:
            controls['ExposureTime'] = int(payload['ExposureTime'])
            config["camera_params"]['ExposureTime'] = controls['ExposureTime']
            config_updated = True
        if 'AnalogueGain' in payload:
            controls['AnalogueGain'] = float(payload['AnalogueGain'])
            config["camera_params"]['AnalogueGain'] = controls['AnalogueGain']
            config_updated = True
        if 'ColourGains' in payload:
            # Format expected: [red_gain, blue_gain]
            gains = payload['ColourGains']
            if isinstance(gains, list) and len(gains) == 2:
                controls['ColourGains'] = (float(gains[0]), float(gains[1]))
                config["camera_params"]['ColourGains'] = gains
                config_updated = True
        if 'LensPosition' in payload:
            controls['LensPosition'] = float(payload['LensPosition'])
            config["camera_params"]['LensPosition'] = controls['LensPosition']
            controls['AfMode'] = 0 # 0 sets AfModeEnum.Manual usually required for LensPosition
            config_updated = True
            
        # Additional settings like AfMode
        if 'AfMode' in payload:
            controls['AfMode'] = int(payload['AfMode'])
            config["camera_params"]['AfMode'] = controls['AfMode']
            config_updated = True
            
        if controls:
            picam2.set_controls(controls)
            print(f"INFO: Applied camera controls: {controls}")
            
        if config_updated:
            # Move save_config to a background thread to prevent blocking MQTT loop
            threading.Thread(target=save_config, daemon=True).start()
            
            # Publish updated parameters immediately
            updated_params = {
                'camera_params': config.get("camera_params", {})
            }
            client.publish(MQTT_TOPIC_STATUS, json.dumps(updated_params))

        # Handle resolution changes
        if 'resolution' in payload:
            res = payload['resolution']
            if isinstance(res, list) and len(res) == 2:
                new_width, new_height = int(res[0]), int(res[1])
                if new_width != current_width or new_height != current_height:
                    with capture_lock:
                        print(f"INFO: Reconfiguring resolution to {new_width}x{new_height}...")
                        picam2.stop()
                        current_width, current_height = new_width, new_height
                        config = picam2.create_preview_configuration(
                            main={'format': 'RGB888', 'size': (current_width, current_height)}
                        )
                        picam2.configure(config)
                        picam2.start()
                        print("INFO: Camera resolution updated.")

        # Capture signal
        if payload.get('action') == 'capture':
            capture_triggered = True
            current_time = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n=======================================================")
            print(f"[{current_time}] 🎯 MQTT TRIGGER RECEIVED: 'capture'")
            print(f"=======================================================\n")


        # System management
        if payload.get('system') == 'restart':
            print("WARNING: Restarting system via MQTT command...")
            os.system("sudo reboot")
            
        if payload.get('system') == 'shutdown':
            print("WARNING: Shutting down system via MQTT command...")
            os.system("sudo halt")

    except json.JSONDecodeError:
        print("ERROR: Invalid JSON received via MQTT")
    except Exception as e:
        print(f"ERROR: Error handling MQTT message: {e}")

def main():
    global picam2, capture_triggered

    # 1. Initialize Camera
    try:
        if args.mock_dir:
            print(f"INFO: Starting in MOCK mode using directory: {args.mock_dir}")
            picam2 = MockCamera(args.mock_dir)
        else:
            picam2 = Picamera2(camera_num=CAMERA_ID)
            cam_config = picam2.create_preview_configuration(
                main={'format': 'RGB888', 'size': (current_width, current_height)}
            )
            picam2.configure(cam_config)
            picam2.start()
            print(f"INFO: Camera started successfully at {current_width}x{current_height}")
            
            # Apply saved generic parameters
            controls = config.get("controls", {})
            if controls:
                picam2.set_controls(controls)
                print(f"INFO: Applied camera controls from config: {controls}")
    except Exception as e:
        print(f"CRITICAL: Failed to initialize camera: {e}")
        os._exit(1) # Exit forcefully with an error code to trigger Systemd Restart

    # Start worker thread for sending images
    worker = threading.Thread(target=image_sender_worker, daemon=True)
    worker.start()

    # 2. Initialize MQTT
    mqtt_client = mqtt.Client()
    
    if MQTT_USERNAME and MQTT_PASSWORD:
        mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        print("INFO: MQTT Authentication configured.")
        
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        # Use non-blocking loop to ensure main thread remains responsive
        mqtt_client.loop_start() 
    except Exception as e:
        print(f"ERROR: Failed to connect to MQTT broker {MQTT_BROKER}: {e}")
        # Not exiting here; might still want camera running or try connecting later
        # A more robust script could implement MQTT reconnect logic

    print("INFO: Sender Service is running. Waiting for commands...")
    
    # 3. Main Loop
    last_status_time = 0
    last_capture_time = 0
    try:
        while True:
            current_time = time.time()
            if current_time - last_status_time >= 5.0:
                status = {
                    'camera_id': CAMERA_ID,
                    'cpu_temp': round(get_cpu_temperature(), 2),
                    'ram_usage_percent': round(get_ram_usage(), 2),
                    'cpu_usage_percent': round(get_cpu_usage(), 2),
                    'resolution': [current_width, current_height],
                    'camera_params': config.get("camera_params", {})
                }
                mqtt_client.publish(MQTT_TOPIC_STATUS, json.dumps(status))
                last_status_time = current_time

            # Trigger a capture either if forced via MQTT or via continuous stream interval
            should_stream = CONTINUOUS_STREAM and (current_time - last_capture_time >= STREAM_INTERVAL)

            if capture_triggered or should_stream:
                with capture_lock:
                    if capture_triggered:
                        print("INFO: Manual capture triggered.")
                        capture_triggered = False
                        
                    try:
                        frame = picam2.capture_array()
                        
                        # Drop old frame if queue is full to maintain real-time
                        if image_queue.full():
                            try:
                                image_queue.get_nowait()
                                image_queue.task_done()
                                print("WARNING: Dropped frame due to full queue.")
                            except: pass
                        image_queue.put(frame)
                        last_capture_time = time.time()
                    except Exception as e:
                        print(f"ERROR: Capture failed: {e}")
            
            # Sleep briefly to yield CPU and avoid 100% usage loop
            time.sleep(LOOP_DELAY)
            
    except KeyboardInterrupt:
        print("INFO: Stopping script (KeyboardInterrupt)...")
    finally:
        print("INFO: Cleaning up resources...")
        mqtt_client.loop_stop()
        if picam2:
            picam2.stop()
        if tcp_socket:
            tcp_socket.close()

if __name__ == "__main__":
    main()