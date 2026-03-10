import json
import libcamera
try:
    from picamera2 import Picamera2
    picam2 = Picamera2()
    config = picam2.create_preview_configuration()
    picam2.configure(config)
    picam2.start()
    controls = picam2.camera.controls
    for k, v in controls.items():
        print(f"{k.name}: {v}")
    picam2.stop()
except Exception as e:
    print(f"Error: {e}")
