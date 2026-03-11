import sys
from picamera2 import Picamera2
picam2 = Picamera2()
config = picam2.create_preview_configuration(main={"size": (2304, 1296), "format": "RGB888"}, lores={"size": (640, 360), "format": "RGB888"})
print(config)
