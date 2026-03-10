import cv2
import argparse
import os

def resize_image(input_path, output_path=None, width=2549, height=1785):
    if not os.path.exists(input_path):
        print(f"Error: Could not find image at {input_path}")
        return

    # Load image
    img = cv2.imread(input_path)
    if img is None:
        print(f"Error: Failed to read image using OpenCV: {input_path}")
        return

    h, w = img.shape[:2]
    print(f"Original size: {w}x{h}")
    
    if w == width and h == height:
        print("Image is already at the target resolution!")
    else:
        # Resize image
        img = cv2.resize(img, (width, height))
        print(f"Resizing to: {width}x{height}")

    # Determine default output name if not specified
    if output_path is None:
        name, ext = os.path.splitext(input_path)
        output_path = f"{name}_{width}x{height}{ext}"

    # Save resized image
    cv2.imwrite(output_path, img)
    print(f"Successfully saved as: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A tool to resize an image (default: 2549x1785)")
    parser.add_argument("input_image", help="Path to the input image file")
    parser.add_argument("-o", "--output", help="Path for the output resized image (optional)", default=None)
    parser.add_argument("-W", "--width", type=int, help="Target width (default: 2549)", default=2549)
    parser.add_argument("-H", "--height", type=int, help="Target height (default: 1785)", default=1785)
    
    args = parser.parse_args()
    resize_image(args.input_image, args.output, args.width, args.height)
