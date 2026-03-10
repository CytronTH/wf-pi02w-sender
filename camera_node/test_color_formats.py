import cv2
import time
from picamera2 import Picamera2

picam2 = Picamera2(camera_num=0)
print("Configuring RGB888...")
cam_config = picam2.create_preview_configuration(main={'format': 'RGB888', 'size': (1280, 720)})
picam2.configure(cam_config)
picam2.start()
time.sleep(1) # warm up

frame_rgb = picam2.capture_array()
print("Captured RGB888")
cv2.imwrite('/home/wf51/pi5_sender/camera_node/static/test_raw.jpg', frame_rgb)
cv2.imwrite('/home/wf51/pi5_sender/camera_node/static/test_cvt.jpg', cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR))

picam2.stop()
print("Configuring BGR888...")
cam_config = picam2.create_preview_configuration(main={'format': 'BGR888', 'size': (1280, 720)})
picam2.configure(cam_config)
picam2.start()
time.sleep(1)

frame_bgr = picam2.capture_array()
print("Captured BGR888")
cv2.imwrite('/home/wf51/pi5_sender/camera_node/static/test_bgr_raw.jpg', frame_bgr)

picam2.stop()
print("Done. Check files in static/")
