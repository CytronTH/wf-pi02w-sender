import json
import os
import cv2
import numpy as np
import argparse

def load_calibration():
    # Resolve the directory of THIS script (src)
    base_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(base_dir) # sender_installer
    
    # Check possible places for crop_4point.json
    config_paths = [
        os.path.join(parent_dir, "configs", "crop_4point.json"),
        os.path.join(base_dir, "crop_4point.json"),
        "configs/crop_4point.json",
        "crop_4point.json"
    ]
    
    config_path = None
    for p in config_paths:
        if os.path.exists(p):
            config_path = p
            break
            
    if not config_path:
        raise ValueError("CRITICAL ERROR: configs/crop_4point.json not found.")
    
    with open(config_path, "r") as f:
        config = json.load(f)
    
    # We should search for templates in the same directory as the config file
    config_dir = os.path.dirname(config_path)
    
    templates = []
    
    # Check parent directory (e.g. sender_installer -> wf51)
    parent_dir = os.path.dirname(os.path.dirname(base_dir))

    for mark in config["calibration_marks"]:
        path = os.path.basename(mark["template"]) # Strip any old paths from json just in case
        
        possible_template_paths = [
            os.path.join(config_dir, "templates", path),
            os.path.join(parent_dir, "configs", "templates", path),
            os.path.join(config_dir, path),
            path
        ]
        
        template_file = None
        for pt in possible_template_paths:
            if os.path.exists(pt):
                template_file = pt
                break
                
        if template_file:
            templates.append(cv2.imread(template_file))
        else:
            raise ValueError(f"CRITICAL ERROR: Template image '{path}' not found! Please ensure all template images exist before running the application.")
            
    return config, templates

def find_mark(img_gray, template, search_roi=None):
    """
    Finds a template in an image.
    search_roi: (x, y, w, h) to limit search. If None, searches full image.
    Returns: (max_loc, max_val) -> ((x,y), score)
    """
    if search_roi:
        x, y, w, h = search_roi
        # Ensure ROI is within bounds
        h_img, w_img = img_gray.shape
        x = max(0, x)
        y = max(0, y)
        w = min(w_img - x, w)
        h = min(h_img - y, h)
        
        roi = img_gray[y:y+h, x:x+w]
        if roi.size == 0 or roi.shape[0] < template.shape[0] or roi.shape[1] < template.shape[1]:
             return None, 0.0
             
        res = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
        
        # Adjust local coordinates to global
        global_x = max_loc[0] + x
        global_y = max_loc[1] + y
        return (global_x, global_y), max_val
    else:
        res = cv2.matchTemplate(img_gray, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
        return max_loc, max_val

def main():
    parser = argparse.ArgumentParser(description="Crop wall boxes using feature-based homography.")
    parser.add_argument("--input", default="images", help="Input directory containing images")
    parser.add_argument("--output", default="dataset/cropy11", help="Output directory for cropped images")
    args = parser.parse_args()

    # Load Calibration
    config, templates = load_calibration()
    if config is None:
        print("Error: Calibration files not found! Run 'calibrate_offsets.py' first.")
        return

    calib_marks = config["calibration_marks"]
    calib_corners = config["calibration_corners"]
    
    # Extract reference points (centers of marks in calibration image)
    ref_mark_points = np.array([[m["x"], m["y"]] for m in calib_marks], dtype=np.float32)
    
    # Extract reference corners (points to crop in calibration image)
    ref_corner_points = np.array([[c["x"], c["y"]] for c in calib_corners], dtype=np.float32)

    # Output directory for crops
    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)

    # Get all jpg images in the 'images' directory
    image_dir = args.input
    if not os.path.exists(image_dir):
        print(f"Error: Directory '{image_dir}' not found.")
        return

    image_files = [f for f in os.listdir(image_dir) if f.lower().endswith('.jpg')]

    if not image_files:
        print(f"No .jpg files found in '{image_dir}'.")
        return

    print(f"Found {len(image_files)} images. Starting 4-Mark Homography Processing...")

    for img_file in image_files:
        img_path = os.path.join(image_dir, img_file)
        print(f"Processing {img_file}...")
        
        original_img = cv2.imread(img_path)
        if original_img is None: continue
        
        img_gray = cv2.cvtColor(original_img, cv2.COLOR_BGR2GRAY)
        
        found_marks = []
        
        # 1. Find Mark 1 (Primary) - Full Search
        # Template 0
        tmpl1 = cv2.cvtColor(templates[0], cv2.COLOR_BGR2GRAY)
        loc, score = find_mark(img_gray, tmpl1)
        
        if score < 0.5:
            print(f"  Result: Mark 1 not found (score {score:.2f}). Skipping.")
            continue
            
        # Get center of found mark 1
        h, w = tmpl1.shape
        m1_cx = loc[0] + w // 2
        m1_cy = loc[1] + h // 2
        found_marks.append([m1_cx, m1_cy])
        
        # 2. Find Marks 2, 3, 4 (Optimized Search)
        # Calculate offset from Mark 1 in calibration image
        ref_m1 = ref_mark_points[0]
        
        for i in range(1, 4):
            tmpl = cv2.cvtColor(templates[i], cv2.COLOR_BGR2GRAY)
            ref_m = ref_mark_points[i]
            
            # Expected vector from M1
            dx = ref_m[0] - ref_m1[0]
            dy = ref_m[1] - ref_m1[1]
            
            # Expected position in current image (assuming roughly translation only for search)
            # A huge rotation might break this ROI search, but usually it's fine for < 20 deg
            exp_cx = m1_cx + dx
            exp_cy = m1_cy + dy
            
            # Define ROI (e.g., +/- 100 pixels)
            search_pad = 150
            roi_x = int(exp_cx - search_pad)
            roi_y = int(exp_cy - search_pad)
            
            # Need top-left of template relative to center
            th, tw = tmpl.shape
            # The find_mark returns top-left of match.
            # We search for the top-left of the match.
            # Expected top-left = exp_center - half_size
            exp_tl_x = exp_cx - tw // 2
            exp_tl_y = exp_cy - th // 2
            
            roi_rect = (int(exp_tl_x - search_pad), int(exp_tl_y - search_pad), search_pad*2 + tw, search_pad*2 + th)
            
            loc, score = find_mark(img_gray, tmpl, roi_rect)
            
            if score < 0.5:
                print(f"  Result: Mark {i+1} not found (score {score:.2f}). Skipping.")
                found_marks = None # Invalidate
                break
            
            cx = loc[0] + tw // 2
            cy = loc[1] + th // 2
            found_marks.append([cx, cy])
            
        if found_marks is None:
            continue
            
        current_mark_points = np.array(found_marks, dtype=np.float32)
        
        # 3. Compute Homography: Map Calibration Space -> Current Image Space
        # We need to transform the "Calibration Corners" into "Current Image Corners"
        # H maps Ref -> Current
        H, _ = cv2.findHomography(ref_mark_points, current_mark_points, cv2.RANSAC, 5.0)
        
        if H is None:
            print("  Result: Homography failed.")
            continue
            
        # 4. Map Calibration Corners to Current Image
        # Reshape for perspectiveTransform: (N, 1, 2)
        ref_corners_reshaped = ref_corner_points.reshape(-1, 1, 2)
        current_corners = cv2.perspectiveTransform(ref_corners_reshaped, H)
        src_crop_points = current_corners.reshape(4, 2).astype(np.float32)
        
        # 5. Final Warp (Straighten the crop)
        # Define straight destination box
        # Width: Top/Bottom avg
        w_top = np.linalg.norm(src_crop_points[0] - src_crop_points[1])
        w_bot = np.linalg.norm(src_crop_points[3] - src_crop_points[2])
        out_w = int((w_top + w_bot) / 2)
        
        # Height: Left/Right avg
        h_left = np.linalg.norm(src_crop_points[0] - src_crop_points[3])
        h_right = np.linalg.norm(src_crop_points[1] - src_crop_points[2])
        out_h = int((h_left + h_right) / 2)
        
        dst_rect = np.array([
            [0, 0],
            [out_w - 1, 0],
            [out_w - 1, out_h - 1],
            [0, out_h - 1]
        ], dtype=np.float32)
        
        # Warp matrix from Current Distorted Corners -> Straight Box
        M_final = cv2.getPerspectiveTransform(src_crop_points, dst_rect)
        
        final_crop = cv2.warpPerspective(original_img, M_final, (out_w, out_h))
        
        # 6. Save
        base_name = os.path.splitext(img_file)[0]
        crop_filename = f"{base_name}_homography.jpg"
        crop_path = os.path.join(output_dir, crop_filename)
        cv2.imwrite(crop_path, final_crop)
        print(f"  Saved crop: {crop_path}")

    print("Processing complete.")

if __name__ == "__main__":
    main()
