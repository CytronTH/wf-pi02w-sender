import cv2
import argparse
from picamera2 import Picamera2
import time
import os

def main():
    parser = argparse.ArgumentParser(description="Capture an image manually for calibration.")
    parser.add_argument("--camera", type=int, default=0, help="Camera ID (0 or 1)")
    parser.add_argument("--output", type=str, default="calibration_target.jpg", help="Output filename")
    parser.add_argument("--width", type=int, default=4608, help="Image width")
    parser.add_argument("--height", type=int, default=2592, help="Image height")
    args = parser.parse_args()

    print(f"Initializing Camera {args.camera} at {args.width}x{args.height}...")
    try:
        picam2 = Picamera2(camera_num=args.camera)
        config = picam2.create_preview_configuration(
            main={'format': 'RGB888', 'size': (args.width, args.height)}
        )
        picam2.configure(config)
        picam2.start()
        
        # Give camera time to auto-expose and focus
        print("Warming up camera for 2 seconds...")
        time.sleep(2)
        
        print(f"Capturing image to {args.output}...")
        img = picam2.capture_array()
        
        # Save as BGR for OpenCV compatibility later
        img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        cv2.imwrite(args.output, img_bgr)
        
        print(f"Success! Image saved as {os.path.abspath(args.output)}")
    except Exception as e:
        print(f"Error capturing image: {e}")
    finally:
        try:
            picam2.stop()
        except:
            pass

if __name__ == '__main__':
    main()
