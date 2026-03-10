import cv2
import json
import os
import argparse
import numpy as np

# Global variables to store points
mark_rois = [] # List of [(x,y), (x,y)] for 4 marks
corners = []   # List of (x,y) for 4 corners
drawing = False
img_display = None
scale_factor = 1.0
temp_roi_start = None

def click_event(event, x, y, flags, param):
    global mark_rois, corners, drawing, img_display, scale_factor, temp_roi_start

    # Map screen coords to original image coords
    orig_x = int(x / scale_factor)
    orig_y = int(y / scale_factor)

    # Step 1: Select 4 Marks (Drag Rectangles)
    if len(mark_rois) < 4:
        if event == cv2.EVENT_LBUTTONDOWN:
            temp_roi_start = (orig_x, orig_y)
            drawing = True
        elif event == cv2.EVENT_MOUSEMOVE and drawing:
            img_copy = img_display.copy()
            # Draw using screen coordinates
            start_screen = (int(temp_roi_start[0] * scale_factor), int(temp_roi_start[1] * scale_factor))
            cv2.rectangle(img_copy, start_screen, (x, y), (0, 255, 0), 2)
            cv2.imshow("Calibrate", img_copy)
        elif event == cv2.EVENT_LBUTTONUP:
            temp_roi_start_pt = temp_roi_start
            temp_roi_end_pt = (orig_x, orig_y)
            mark_rois.append((temp_roi_start_pt, temp_roi_end_pt))
            drawing = False
            
            # Draw final rectangle on display image
            start_screen = (int(temp_roi_start_pt[0] * scale_factor), int(temp_roi_start_pt[1] * scale_factor))
            end_screen = (int(temp_roi_end_pt[0] * scale_factor), int(temp_roi_end_pt[1] * scale_factor))
            cv2.rectangle(img_display, start_screen, end_screen, (0, 255, 0), 2)
            
            # Label the mark
            cv2.putText(img_display, f"M{len(mark_rois)}", (start_screen[0], start_screen[1]-10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
            
            cv2.imshow("Calibrate", img_display)
            print(f"Mark {len(mark_rois)} Selected.")
            
            if len(mark_rois) == 4:
                print("All 4 Marks selected. Now click the 4 corners of the Wall Box (TL, TR, BR, BL).")

    # Step 2: Select 4 Corners
    elif len(corners) < 4:
        if event == cv2.EVENT_LBUTTONDOWN:
            corners.append((orig_x, orig_y))
            # Draw circle using screen coordinates
            cv2.circle(img_display, (x, y), 5, (0, 0, 255), -1)
            # Label the corner
            labels = ["TL", "TR", "BR", "BL"]
            cv2.putText(img_display, labels[len(corners)-1], (x+10, y), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
            
            cv2.imshow("Calibrate", img_display)
            print(f"Corner {len(corners)} recorded: {orig_x}, {orig_y}")
            
            if len(corners) == 4:
                print("All points selected! Press 's' to save or 'r' to reset.")

def calibrate(image_path):
    global img_display, mark_rois, corners, scale_factor
    
    if not os.path.exists(image_path):
        print(f"Error: Image {image_path} not found.")
        return

    original_img = cv2.imread(image_path)
    
    # Calculate scale factor to fit screen (height max 800)
    height, width = original_img.shape[:2]
    max_height = 800
    
    if height > max_height:
        scale_factor = max_height / height
        new_width = int(width * scale_factor)
        new_height = int(height * scale_factor)
        img_display = cv2.resize(original_img, (new_width, new_height))
        print(f"Image resized for display (Scale: {scale_factor:.2f})")
    else:
        scale_factor = 1.0
        img_display = original_img.copy()

    cv2.namedWindow("Calibrate")
    cv2.setMouseCallback("Calibrate", click_event)

    print("---------------------------------------------------------")
    print("INSTRUCTIONS:")
    print("1. Drag boxes around 4 MARKS sequentially (M1, M2, M3, M4).")
    print("   (e.g., Top-Left, Top-Right, Bottom-Right, Bottom-Left features)")
    print("2. Click the 4 CORNERS of the crop area (Red Dots) in order:")
    print("   [Top-Left, Top-Right, Bottom-Right, Bottom-Left]")
    print("3. Press 's' to Save Config, 'r' to Reset, 'q' to Quit.")
    print("---------------------------------------------------------")

    cv2.imshow("Calibrate", img_display)

    while True:
        # cv2.imshow("Calibrate", img_display) # Moved out to avoid flicker
        key = cv2.waitKey(1) & 0xFF

        if key == ord("s"):
            if len(mark_rois) == 4 and len(corners) == 4:
                save_calibration(original_img)
                break
            else:
                print(f"Incomplete! Marks: {len(mark_rois)}/4, Corners: {len(corners)}/4")
        
        elif key == ord("r"):
            # Reset
            if height > max_height:
                new_width = int(width * scale_factor)
                new_height = int(height * scale_factor)
                img_display = cv2.resize(original_img, (new_width, new_height))
            else:
                img_display = original_img.copy()
            
            mark_rois = []
            corners = []
            print("Reset.")

        elif key == ord("q"):
            print("Cancelled.")
            break

    cv2.destroyAllWindows()

def save_calibration(img):
    global mark_rois, corners
    
    # Resolve project root dir and configs dir
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    configs_dir = os.path.join(project_root, "configs")
    templates_dir = os.path.join(configs_dir, "templates")
    
    os.makedirs(templates_dir, exist_ok=True)
    
    # 1. Save 4 Mark Templates
    marks_data = []
    
    for i, roi in enumerate(mark_rois):
        x1, y1 = roi[0]
        x2, y2 = roi[1]
        
        # Ensure correct order
        x_start, x_end = sorted([x1, x2])
        y_start, y_end = sorted([y1, y2])
        
        template = img[y_start:y_end, x_start:x_end]
        filename = f"mark{i+1}_template.jpg"
        filepath = os.path.join(templates_dir, filename)
        cv2.imwrite(filepath, template)
        print(f"Saved '{filepath}'")
        
        # Store center of mark
        cx = (x_start + x_end) // 2
        cy = (y_start + y_end) // 2
        marks_data.append({"id": f"mark{i+1}", "x": cx, "y": cy, "template": filename})

    # 2. Store Topology
    # We store the absolute coordinates of Marks and Corners in this reference image.
    # The runtime script will find the Marks, compute Homography H, 
    # and then map these Corner coordinates using H.
    
    corner_labels = ["TL", "TR", "BR", "BL"]
    corners_data = [{"point": corner_labels[i], "x": pt[0], "y": pt[1]} for i, pt in enumerate(corners)]

    config = {
        "calibration_marks": marks_data,
        "calibration_corners": corners_data
    }

    config_path = os.path.join(configs_dir, "calibration_points.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=4)
    
    print(f"Saved '{config_path}'")
    print("Calibration Complete!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("image_path", help="Path to a representative image")
    args = parser.parse_args()
    
    calibrate(args.image_path)
