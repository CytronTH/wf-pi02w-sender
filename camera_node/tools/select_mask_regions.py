import cv2
import json
import argparse
import os
import glob

def select_masks(image_path, output_config="configs/crop_regions.json"):
    # 1. Load Image
    if not os.path.exists(image_path):
        print(f"Error: Image {image_path} not found.")
        return

    img = cv2.imread(image_path)
    h, w = img.shape[:2]
    
    # Resize Logic
    MAX_W, MAX_H = 1600, 900
    scale = 1.0
    if w > MAX_W or h > MAX_H:
        scale = min(MAX_W/w, MAX_H/h)
        new_w, new_h = int(w*scale), int(h*scale)
        img_display = cv2.resize(img, (new_w, new_h))
        print(f"Resizing for display: {w}x{h} -> {new_w}x{new_h} (Scale: {scale:.4f})")
    else:
        img_display = img.copy()

    print("---------------------------------------------------------")
    print("INSTRUCTIONS:")
    print("1. A window will open for EACH mask region.")
    print("2. Draw the box and press SPACE/ENTER to confirm.")
    print("3. Press 'c' to cancel current selection (and finish).")
    print("4. The loop continues until you press 'c' or close the window.")
    print("   (You can select as many areas as needed, not limited to 4)")
    print("---------------------------------------------------------")

    mask_regions = []
    i = 0
    while True:
        i += 1
        window_name = f"Select Mask Region #{i} (Draw -> SPACE. Press 'c' to stop)"
        print(f"\n--- ROI Selection #{i} ---")
        
        # roi = (x, y, w, h)
        roi = cv2.selectROI(window_name, img_display, showCrosshair=True, fromCenter=False)
        cv2.destroyWindow(window_name)
        
        # Check if user cancelled (all zeros)
        if roi == (0, 0, 0, 0):
            print("Selection cancelled or finished.")
            break
            
        x, y, w_box, h_box = roi
        
        # Scale back
        real_x = int(x / scale)
        real_y = int(y / scale)
        real_w = int(w_box / scale)
        real_h = int(h_box / scale)
        
        mask_regions.append({
            "id": f"mask_{i}",
            "x": real_x,
            "y": real_y,
            "w": real_w,
            "h": real_h
        })
        print(f"Mask #{i} Confirmed: x={real_x}, y={real_y}, w={real_w}, h={real_h}")
        
    cv2.destroyAllWindows()
    
    if len(mask_regions) == 0:
        print("No areas selected.")
        return

    # Save to JSON
    config = {
        "mask_regions": mask_regions,
        "reference_image_size": {
            "width": img.shape[1],
            "height": img.shape[0]
        }
    }
    
    # Resolve the project root for saving configs correctly regardless of cwd
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    if not os.path.isabs(output_config):
        output_config = os.path.join(project_root, output_config)
        
    os.makedirs(os.path.dirname(output_config), exist_ok=True)
    
    with open(output_config, "w") as f:
        json.dump(config, f, indent=4)
        
    print(f"Saved {len(mask_regions)} mask regions to {output_config}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tool to manually select mask regions.")
    parser.add_argument("--image", help="Path to a sample ALIGNED image to use as reference", default=None)
    
    args = parser.parse_args()
    
    target_img = args.image
    if target_img is None:
        # Try to find one automatically
        files = glob.glob("dataset/aligned/*.jpg")
        if files:
            target_img = files[0]
            print(f"Auto-detected aligned image: {target_img}")
        else:
            print("Error: No image provided and none found in dataset/aligned/")
            exit(1)
            
    select_masks(target_img)
