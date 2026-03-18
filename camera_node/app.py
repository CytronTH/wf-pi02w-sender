import os
import time
import json
import threading
import subprocess
import cv2
import socket
import collections
from flask import Flask, render_template, Response, jsonify, request, send_file
from picamera2 import Picamera2
import psutil
import datetime

app = Flask(__name__)

# --- Global State ---
# Base directory for config paths
base_dir = os.path.dirname(os.path.abspath(__file__))

# Manage state for camera
CAMERAS = {
    "cam0": {
        "device_id": 0,
        "config_path": os.path.join(base_dir, 'configs', 'config_cam0.json'),
        "mode": "tcp",
        "picam2": None,
        "tcp_process": None,
        "logs": collections.deque(maxlen=200),
        "lock": threading.Lock()
    }
}

# --- Helper Functions ---
def get_camera_settings(cam_id):
    """Load default dimensions and camera controls from the config file."""
    width, height = 2304, 1296
    controls = {}
    try:
        cfg_path = CAMERAS[cam_id]["config_path"]
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r') as f:
                config = json.load(f)
                cam_cfg = config.get("camera", {})
                width = cam_cfg.get("default_width", 2304)
                height = cam_cfg.get("default_height", 1296)
                controls = config.get("controls", {})
    except Exception as e:
        print(f"Warning: Could not read config file for {cam_id}: {e}")
    return width, height, controls

def start_picamera(cam_id):
    """Initialize and start picamera2 for WebUI streaming for a specific camera."""
    cam_data = CAMERAS[cam_id]
    print(f"INFO: Starting Picamera2 for WebUI ({cam_id})...")
    try:
        if cam_data["picam2"] is None:
            cam_data["picam2"] = Picamera2(camera_num=cam_data["device_id"])
            
        width, height, controls = get_camera_settings(cam_id)
        # For Pi Zero 2W, we force a lower resolution (e.g., 640x360) 
        # for the WebUI preview to prevent out-of-memory or CPU hanging
        # when capturing and encoding RGB arrays. 
        # The main TCP Sender will still use the full configured resolution.
        preview_width, preview_height = 640, 360
        cam_config = cam_data["picam2"].create_preview_configuration(
            main={'format': 'RGB888', 'size': (preview_width, preview_height)},
            raw={'size': (width, height)}
        )
        cam_data["picam2"].configure(cam_config)
        cam_data["picam2"].start()
        
        # Apply Custom Camera Controls if available
        if controls:
            try:
                cam_data["picam2"].set_controls(controls)
                print(f"INFO: Applied camera controls: {controls}")
            except Exception as ce:
                print(f"ERROR: Failed to apply camera controls on {cam_id}: {ce}")
        
        # Extract Sensor Name
        raw_id = cam_data["picam2"].camera.id
        if '/' in raw_id and '@' in raw_id:
            # Typically: /base/axi/pcie@1000120000/rp1/i2c@88000/imx708@1a -> imx708
            cam_data["sensor_name"] = raw_id.split('/')[-1].split('@')[0].upper()
        else:
            cam_data["sensor_name"] = raw_id
            
        print(f"INFO: Picamera2 ({cam_id}) started successfully. Sensor: {cam_data.get('sensor_name')}")
        return True
    except Exception as e:
        print(f"ERROR: Failed to start Picamera2 for {cam_id}: {e}")
        cam_data["picam2"] = None
        return False

def stop_picamera(cam_id):
    """Stop and release picamera2 for a specific camera."""
    cam_data = CAMERAS[cam_id]
    print(f"INFO: Stopping Picamera2 ({cam_id})...")
    if cam_data["picam2"] is not None:
        try:
            cam_data["picam2"].stop()
            cam_data["picam2"].close()
        except Exception as e:
            print(f"Warning: Error while stopping camera {cam_id}: {e}")
        finally:
            cam_data["picam2"] = None
            print(f"INFO: Picamera2 ({cam_id}) stopped and camera resource released.")

def stream_reader(process, logs_queue):
    """Reads stdout from a subprocess and puts it into a deque."""
    for line in iter(process.stdout.readline, ''):
        if line:
            logs_queue.append(line.rstrip())
    process.stdout.close()

def start_tcp_sender(cam_id):
    """Start the main.py script as a subprocess for a specific camera."""
    cam_data = CAMERAS[cam_id]
    cfg_path = cam_data["config_path"]
    
    if cam_data["tcp_process"] is None or cam_data["tcp_process"].poll() is not None:
        print(f"INFO: Starting TCP Sender Subprocess ({cam_id}): python3 main.py -c {cfg_path}")
        cam_data["logs"].clear()
        try:
            cam_data["tcp_process"] = subprocess.Popen(
                ['python3', '-u', 'main.py', '-c', cfg_path], # Make python stdout unbuffered
                cwd=base_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            # Start reader daemon thread
            t = threading.Thread(target=stream_reader, args=(cam_data["tcp_process"], cam_data["logs"]), daemon=True)
            t.start()
            
            print(f"INFO: TCP Sender ({cam_id}) started with PID {cam_data['tcp_process'].pid}")
            return True
        except Exception as e:
            print(f"ERROR: Failed to start TCP Sender for {cam_id}: {e}")
            return False
    return True

def stop_tcp_sender(cam_id):
    """Terminate the main.py subprocess if it's running for a specific camera."""
    cam_data = CAMERAS[cam_id]
    print(f"INFO: Stopping TCP Sender Subprocess ({cam_id})...")
    if cam_data["tcp_process"] is not None and cam_data["tcp_process"].poll() is None:
        cam_data["logs"].append(f"INFO: Stopping TCP process PID {cam_data['tcp_process'].pid}...")
        try:
            cam_data["tcp_process"].terminate()
            try:
                cam_data["tcp_process"].wait(timeout=5)
            except subprocess.TimeoutExpired:
                print(f"Warning: Process {cam_id} did not terminate gracefully, forcing kill.")
                cam_data["tcp_process"].kill()
        except Exception as e:
            print(f"Warning: Error while killing TCP Sender {cam_id}: {e}")
            cam_data["logs"].append(f"ERROR: Stop failed exception: {str(e)}")
        finally:
            print(f"INFO: TCP Sender ({cam_id}) stopped.")
            cam_data["logs"].append(f"INFO: Stopped TCP process.")
    cam_data["tcp_process"] = None

# --- Camera Generator ---
def generate_frames(cam_id):
    """Generator function that yields JPEG frames from Picamera2."""
    cam_data = CAMERAS[cam_id]
    while True:
        with cam_data["lock"]:
            if cam_data["mode"] != 'webui' or cam_data["picam2"] is None:
                # If not in WebUI mode, yield nothing or sleep
                time.sleep(1)
                continue

            try:
                # Capture frame from the camera
                frame = cam_data["picam2"].capture_array()
                
                # Add resolution label to the bottom right corner
                h, w = frame.shape[:2]
                text = f"{w}x{h}"
                font = cv2.FONT_HERSHEY_SIMPLEX
                font_scale = 0.5 if w < 1000 else 1.0
                thickness = 1 if w < 1000 else 2
                text_size, _ = cv2.getTextSize(text, font, font_scale, thickness)
                text_w, text_h = text_size
                org = (w - text_w - 10, h - 10)
                
                # Draw black background rectangle for better visibility
                cv2.rectangle(frame, (org[0] - 5, org[1] - text_h - 5), (w, h), (0, 0, 0), -1)
                cv2.putText(frame, text, org, font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)

                # Encode to JPEG
                ret, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if not ret:
                    time.sleep(0.1)
                    continue
                    
                frame_bytes = buffer.tobytes()
                
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                
                time.sleep(0.05) 
            except Exception as e:
                print(f"ERROR: Frame capture error on {cam_id}: {e}")
                time.sleep(1)

# --- API Routes for Config ---
@app.route('/api/logs/<cam_id>', methods=['GET'])
def get_logs(cam_id):
    """Return the recent subprocess logs for a specific camera."""
    if cam_id not in CAMERAS:
        return jsonify({"error": "Invalid camera ID"}), 400
    return jsonify(list(CAMERAS[cam_id]["logs"]))

@app.route('/api/config/<cam_id>', methods=['GET'])
def get_config(cam_id):
    """Returns the current config from disk."""
    if cam_id not in CAMERAS:
        return jsonify({"error": "Invalid camera ID"}), 400
        
    try:
        cfg_path = CAMERAS[cam_id]["config_path"]
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r') as f:
                return jsonify(json.load(f))
        else:
            return jsonify({"error": "Config file not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/config/<cam_id>', methods=['POST'])
def save_config(cam_id):
    """Receives, validates, and saves new config to disk."""
    if cam_id not in CAMERAS:
        return jsonify({"error": "Invalid camera ID"}), 400
        
    try:
        new_config = request.json
        if not new_config:
            return jsonify({"error": "No JSON payload provided"}), 400
            
        required_sections = ["tcp", "mqtt", "camera"]
        for section in required_sections:
            if section not in new_config:
                new_config[section] = {}

        cfg_path = CAMERAS[cam_id]["config_path"]
        with open(cfg_path, 'w') as f:
            json.dump(new_config, f, indent=4)
            
        print(f"INFO: Config file for {cam_id} updated via WebUI.")

        cam_data = CAMERAS[cam_id]
        with cam_data["lock"]:
            if cam_data["mode"] == 'tcp':
                print(f"INFO: Restarting TCP Sender {cam_id} to apply new configuration...")
                stop_tcp_sender(cam_id)
                time.sleep(1)
                start_tcp_sender(cam_id)
                
        return jsonify({"status": "success", "message": "Configuration saved successfully."})
        
    except Exception as e:
        print(f"ERROR saving config for {cam_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/config/<cam_id>/camera_controls', methods=['POST'])
def update_camera_controls(cam_id):
    """Receives camera control properties, saves them, and applies immediately if streaming."""
    if cam_id not in CAMERAS:
        return jsonify({"error": "Invalid camera ID"}), 400
        
    try:
        controls_update = request.json
        if not controls_update:
            return jsonify({"error": "No JSON payload provided"}), 400
            
        cfg_path = CAMERAS[cam_id]["config_path"]
        
        # Load existing config
        config = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r') as f:
                config = json.load(f)
                
        # Update or create the controls namespace
        if "controls" not in config:
            config["controls"] = {}
            
        config["controls"].update(controls_update)
        
        # Save seamlessly without destroying stream mode
        with open(cfg_path, 'w') as f:
            json.dump(config, f, indent=4)
            
        print(f"INFO: Camera controls for {cam_id} updated: {controls_update}")
        
        # Apply the new controls instantly if picam2 is actively streaming in WebUI
        cam_data = CAMERAS[cam_id]
        with cam_data["lock"]:
            if cam_data["mode"] == "webui" and cam_data["picam2"] is not None:
                try:
                    cam_data["picam2"].set_controls(config["controls"])
                    print(f"INFO: Applied dynamic controls to active stream.")
                except Exception as ce:
                    print(f"ERROR: Failed to apply dynamic controls on {cam_id}: {ce}")
                    
        return jsonify({"status": "success", "message": "Camera controls updated and saved."})
        
    except Exception as e:
        print(f"ERROR updating camera controls for {cam_id}: {e}")
        return jsonify({"error": str(e)}), 500

# --- Routes removed ---

# --- Routes ---
@app.route('/api/system_stats', methods=['GET'])
def system_stats():
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                temp_c = float(f.read().strip()) / 1000.0
        except Exception:
            temp_c = 0.0

        return jsonify({
            "status": "success",
            "cpu": cpu,
            "ram": ram.percent,
            "temp": round(temp_c, 1),
            "disk_percent": disk.percent,
            "disk_free_gb": round(disk.free / (1024**3), 1),
            "disk_total_gb": round(disk.total / (1024**3), 1)
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/')
def index():
    """Render the main WebUI."""
    hostname = socket.gethostname()
    # We pass the dictionary of modes to the template although JS will fetch it anyway
    modes = {k: v["mode"] for k, v in CAMERAS.items()}
    return render_template('index.html', modes=modes, hostname=hostname)

@app.route('/video_feed/<cam_id>')
def video_feed(cam_id):
    """Video streaming route."""
    if cam_id not in CAMERAS:
        return "Camera ID not found", 404
        
    if CAMERAS[cam_id]["mode"] == 'webui':
        return Response(generate_frames(cam_id), mimetype='multipart/x-mixed-replace; boundary=frame')
    else:
        return Response("Camera is currently allocated to TCP Sender.", status=409)

def get_camera_display_name(cam_id):
    """Load the custom display name from the config file, fallback to default."""
    display_name = f"Camera {CAMERAS[cam_id]['device_id']}"
    try:
        cfg_path = CAMERAS[cam_id]["config_path"]
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r') as f:
                config = json.load(f)
                custom_name = config.get("camera", {}).get("name")
                if custom_name:
                    display_name = custom_name
    except:
        pass
    return display_name

@app.route('/debug/<cam_id>')
def debug_view(cam_id):
    """Serve the debug log viewer page."""
    if cam_id not in CAMERAS:
        return "Camera ID not found", 404
    hostname = socket.gethostname()
    display_name = get_camera_display_name(cam_id)
    server_start_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return render_template('debug.html', cam_id=cam_id, hostname=hostname, display_name=display_name, start_time=server_start_time)

@app.route('/status')
def status():
    """Return the current system status for all cameras."""
    res = {}
    for cid, cam in CAMERAS.items():
        res[cid] = {
            "mode": cam["mode"],
            "tcp_pid": cam["tcp_process"].pid if cam["tcp_process"] and cam["tcp_process"].poll() is None else None,
            "sensor_name": cam.get("sensor_name", "Unknown"),
            "display_name": get_camera_display_name(cid)
        }
    return jsonify(res)

@app.route('/switch_mode', methods=['POST'])
def switch_mode():
    """API endpoint to switch modes per camera."""
    data = request.json
    target_mode = data.get('mode')
    cam_id = data.get('cam_id')
    
    if target_mode not in ['webui', 'tcp'] or cam_id not in CAMERAS:
        return jsonify({"error": "Invalid mode or camera ID"}), 400
        
    cam_data = CAMERAS[cam_id]
    
    with cam_data["lock"]:
        if target_mode == cam_data["mode"]:
            return jsonify({"status": "Mode already active", "mode": cam_data["mode"]})
            
        print(f"\n=========================================")
        print(f"[{cam_id}] SWITCHING MODE: {cam_data['mode']} -> {target_mode}")
        print(f"=========================================\n")
        
        if target_mode == 'tcp':
            stop_picamera(cam_id)
            success = start_tcp_sender(cam_id)
            if success:
                cam_data["mode"] = 'tcp'
            else:
                start_picamera(cam_id)
                
        elif target_mode == 'webui':
            stop_tcp_sender(cam_id)
            time.sleep(2)
            success = start_picamera(cam_id)
            if success:
                cam_data["mode"] = 'webui'
            else:
                time.sleep(3)
                start_picamera(cam_id)
                cam_data["mode"] = 'webui' 
                
    return jsonify({"status": "success", "mode": cam_data["mode"]})

if __name__ == '__main__':
    # Initialize initial state for all cameras
    for cid, cam in CAMERAS.items():
        with cam["lock"]:
            if cam["mode"] == 'webui':
                start_picamera(cid)
            elif cam["mode"] == 'tcp':
                start_tcp_sender(cid)
            
    # Run the Flask app on all interfaces, port 5000
    app.run(host='0.0.0.0', port=5000, threaded=True)
