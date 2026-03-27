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
import datetime

import sys
base_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(base_dir, 'src'))

import sftp_handler

# Pre-processing modules removed for Pi Zero 2W performance

# Ensure local capture directory
CAPTURE_DIR = os.path.join(base_dir, 'logs', 'captures')
os.makedirs(CAPTURE_DIR, exist_ok=True)
pending_transfers = []

# --- Configuration Loader ---
parser = argparse.ArgumentParser(description="Camera Sender Script")
parser.add_argument('-c', '--config', type=str, default=os.path.join(base_dir, 'configs', 'config.json'), help='Path to config file')
parser.add_argument('--mock_dir', type=str, default=None, help='Directory containing mock images for offline testing')
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
        "sftp": {
            "sftp_enabled": False,
            "host": "",
            "port": 22,
            "username": "",
            "password": "",
            "remote_path": "/upload/images",
            "batch_size": 3
        }
    }
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
if MQTT_TOPIC_CMD.startswith("wf52/"):
    MQTT_TOPIC_CMD = f"{hostname}/" + MQTT_TOPIC_CMD.split("/", 1)[1]
elif "{hostname}" in MQTT_TOPIC_CMD:
    MQTT_TOPIC_CMD = MQTT_TOPIC_CMD.replace("{hostname}", hostname)

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
image_queue = Queue(maxsize=7)
last_mock_image_name = None

class MockCamera:
    def __init__(self, image_dir):
        self.images = glob.glob(os.path.join(image_dir, '*.jpg'))
        self.images.sort()
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

def get_cpu_temperature():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return float(f.read().strip()) / 1000.0
    except Exception:
        return 0.0

def save_config():
    try:
        with open(args.config, 'w') as f:
            json.dump(config, f, indent=4)
        print(f"INFO: Saved updated configuration to {args.config}")
    except Exception as e:
        print(f"ERROR: Failed to save config to {args.config}: {e}")

last_cpu_idle = 0
last_cpu_total = 0

def get_cpu_usage():
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
        if total == total_diff: 
            return 0.0
        if total_diff > 0:
            return (total_diff - idle_diff) / total_diff * 100.0
        return 0.0
    except Exception:
        return 0.0

def get_ram_usage():
    try:
        with open('/proc/meminfo', 'r') as mem:
            mem_info = mem.readlines()
        mem_total = 0
        mem_free = 0
        mem_buffers = 0
        mem_cached = 0
        for line in mem_info:
            if line.startswith('MemTotal:'): mem_total = int(line.split()[1])
            elif line.startswith('MemFree:'): mem_free = int(line.split()[1])
            elif line.startswith('Buffers:'): mem_buffers = int(line.split()[1])
            elif line.startswith('Cached:'): mem_cached = int(line.split()[1])
        used_memory = mem_total - mem_free - mem_buffers - mem_cached
        if mem_total > 0:
            return (used_memory / mem_total) * 100.0
        return 0.0
    except Exception:
        return 0.0

def connect_tcp():
    global tcp_socket
    if tcp_socket:
        try: tcp_socket.close()
        except: pass
    try:
        tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_socket.settimeout(5.0)
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
        result, encoded_frame = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not result:
            print("ERROR: Failed to encode image")
            return
        data = encoded_frame.tobytes()
        img_size = len(data)
        metadata = {"id": image_id, "size": img_size}
        metadata_json = json.dumps(metadata).encode('utf-8')
        meta_size = len(metadata_json)
        header = struct.pack(">L", meta_size)
        tcp_socket.sendall(header + metadata_json + data)
        print(f"INFO: Sent {image_id}: {current_width}x{current_height} ({img_size} bytes)")
    except Exception as e:
        print(f"ERROR: TCP Send Error: {e}")
        if tcp_socket: tcp_socket.close()
        tcp_socket = None

def image_sender_worker():
    print("INFO: Image sender worker thread started.")
    while True:
        try:
            frame = image_queue.get()
            send_image(frame, image_id="raw_image")
            image_queue.task_done()
        except Exception as e:
            print(f"CRITICAL ERROR: Unexpected Image Sender worker failure: {e}")
            os._exit(1)

def on_mqtt_connect(client, userdata, flags, rc):
    print(f"INFO: Connected to MQTT broker with result code {rc}")
    client.subscribe(MQTT_TOPIC_CMD)
    print(f"INFO: Subscribed to MQTT topic: {MQTT_TOPIC_CMD}")

def on_mqtt_message(client, userdata, msg):
    global capture_triggered, current_width, current_height, picam2, config
    try:
        payload = json.loads(msg.payload.decode())
        if not picam2: return

        controls = {}
        config_updated = False
        if "camera_params" not in config: config["controls"] = {}
            
        if 'ExposureTime' in payload:
            controls['ExposureTime'] = int(payload['ExposureTime'])
            config["controls"]['ExposureTime'] = controls['ExposureTime']
            config_updated = True
        if 'AnalogueGain' in payload:
            controls['AnalogueGain'] = float(payload['AnalogueGain'])
            config["controls"]['AnalogueGain'] = controls['AnalogueGain']
            config_updated = True
        if 'ColourGains' in payload:
            gains = payload['ColourGains']
            if isinstance(gains, list) and len(gains) == 2:
                controls['ColourGains'] = (float(gains[0]), float(gains[1]))
                config["controls"]['ColourGains'] = gains
                config_updated = True
        if 'LensPosition' in payload:
            controls['LensPosition'] = float(payload['LensPosition'])
            config["controls"]['LensPosition'] = controls['LensPosition']
            controls['AfMode'] = 0
            config['controls']['AfMode'] = 0
            config_updated = True
        if 'AfMode' in payload:
            controls['AfMode'] = int(payload['AfMode'])
            config["controls"]['AfMode'] = controls['AfMode']
            config_updated = True
            
        if controls:
            picam2.set_controls(controls)
        if config_updated:
            threading.Thread(target=save_config, daemon=True).start()
            updated_params = {'camera_params': config.get("controls", {})}
            client.publish(MQTT_TOPIC_STATUS, json.dumps(updated_params))

        if 'resolution' in payload:
            res = payload['resolution']
            if isinstance(res, list) and len(res) == 2:
                new_width, new_height = int(res[0]), int(res[1])
                if new_width != current_width or new_height != current_height:
                    with capture_lock:
                        picam2.stop()
                        current_width, current_height = new_width, new_height
                        config_cam = picam2.create_preview_configuration(
                            main={'format': 'RGB888', 'size': (current_width, current_height)}
                        )
                        picam2.configure(config_cam)
                        picam2.start()

        if payload.get('action') == 'capture':
            capture_triggered = True

        if payload.get('system') == 'restart': os.system("sudo reboot")
        if payload.get('system') == 'shutdown': os.system("sudo halt")

    except json.JSONDecodeError:
        print("ERROR: Invalid JSON received via MQTT")
    except Exception as e:
        print(f"ERROR: Error handling MQTT message: {e}")

def main():
    global picam2, capture_triggered, pending_transfers

    try:
        if args.mock_dir:
            picam2 = MockCamera(args.mock_dir)
        else:
            picam2 = Picamera2(camera_num=CAMERA_ID)
            cam_config = picam2.create_preview_configuration(
                main={'format': 'RGB888', 'size': (current_width, current_height)}
            )
            picam2.configure(cam_config)
            picam2.start()
            controls = config.get("controls", {})
            if controls: picam2.set_controls(controls)
    except Exception as e:
        print(f"CRITICAL: Failed to initialize camera: {e}")
        os._exit(1)

    worker = threading.Thread(target=image_sender_worker, daemon=True)
    worker.start()

    mqtt_client = mqtt.Client()
    if MQTT_USERNAME and MQTT_PASSWORD:
        mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
        mqtt_client.loop_start() 
    except Exception as e:
        print(f"ERROR: Failed to connect to MQTT broker {MQTT_BROKER}: {e}")

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
                    'camera_params': config.get("controls", {})
                }
                mqtt_client.publish(MQTT_TOPIC_STATUS, json.dumps(status))
                last_status_time = current_time

            should_stream = CONTINUOUS_STREAM and (current_time - last_capture_time >= STREAM_INTERVAL)

            if capture_triggered or should_stream:
                with capture_lock:
                    if capture_triggered:
                        manual_capture = True
                        capture_triggered = False
                    else:
                        manual_capture = False
                        
                    try:
                        frame = picam2.capture_array()
                        
                        # SFTP Handle Start
                        sftp_cfg = config.get("sftp", {})
                        if manual_capture and sftp_cfg.get("sftp_enabled", False):
                            fraction = f"{time.time() - int(time.time()):.3f}"[2:]
                            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                            filename = f"cam{CAMERA_ID}_{timestamp}_{fraction}.jpg"
                            local_path = os.path.join(CAPTURE_DIR, filename)
                            
                            # Save frame locally first per requirement
                            cv2.imwrite(local_path, frame)
                            pending_transfers.append(local_path)
                            
                            batch_size = sftp_cfg.get("batch_size", 3)
                            if len(pending_transfers) >= batch_size:
                                files_to_upload = list(pending_transfers)
                                pending_transfers.clear()
                                uploader = sftp_handler.SFTPHandler(sftp_cfg)
                                t = threading.Thread(target=uploader.upload_files, args=(files_to_upload,), daemon=True)
                                t.start()
                        # SFTP Handle End
                        
                        if image_queue.full():
                            try:
                                image_queue.get_nowait()
                                image_queue.task_done()
                            except: pass
                        image_queue.put(frame)
                        last_capture_time = time.time()
                    except Exception as e:
                        print(f"ERROR: Capture failed: {e}")
            
            time.sleep(LOOP_DELAY)
            
    except KeyboardInterrupt:
        pass
    finally:
        mqtt_client.loop_stop()
        if picam2: picam2.stop()
        if tcp_socket: tcp_socket.close()

if __name__ == "__main__":
    main()
