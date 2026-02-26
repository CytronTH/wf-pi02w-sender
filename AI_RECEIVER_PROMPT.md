# AI Receiver Prompt: Dual Camera TCP Stream Listener

**Context & Objective:**
You are an AI Software Engineer responsible for developing the Receiver & AI Inference system on a Raspberry Pi. Your task is to write a Python script that acts as a robust TCP server to receive incoming image streams from two separate Camera Sender boards running in parallel (Dual Camera Setup). You need to decode these images and pass them into your AI inference pipeline.

**Architecture & Protocol Requirements:**
The two Sender boards will stream images over TCP with the following architecture:

1. **Dual Ports (Process Isolation):** 
   - Camera 0 streams to TCP Port `8080`
   - Camera 1 streams to TCP Port `8081`
   - Your program must run **two concurrent TCP Listeners (using multi-threading or asyncio)** so that the processing of one camera does not block the other.

2. **Synchronization Trigger:** 
   - Both cameras are triggered simultaneously via a centralized MQTT topic (`camera/command`) using the payload `{"action": "capture"}`.
   - Therefore, images arriving at port `8080` and `8081` within the same timeframe belong to the exact same physical moment.

3. **TCP Image Streaming Protocol (CRITICAL):** 
   - When a camera is triggered, it sends *multiple* images consecutively over the same TCP connection (e.g., 1 full masked image followed by 6 cropped images).
   - Each individual image in the stream is packed using a strict 3-part protocol:
     - **Part 1 (Header):** `4 bytes` (format: `>L` unsigned long) indicating the exact size of the incoming JSON Metadata.
     - **Part 2 (JSON Metadata):** Read exactly the number of bytes specified by the Header. Decode it as a UTF-8 string and parse the JSON. It will look like `{"id": "masked_surface", "size": 15420}`. The `size` value here is the file size of the incoming JPEG image.
     - **Part 3 (JPEG Data):** Read exactly the number of bytes specified by the `size` value from the JSON. This is the raw JPEG data that you can decode using `cv2.imdecode`.
   - The stream will then immediately loop back to Part 1 for the next image (e.g., `crop_0`).

**Your Tasks:**
1. Write a Python Receiver script that concurrently listens on ports `8080` and `8081`.
2. Implement a highly robust `receive_image_stream(connection)` function. It must accurately read the exact number of bytes for the Header, JSON, and JPEG data in a loop. It must handle TCP fragmentation (using a reliable `recvall` helper function) to ensure data is never misaligned.
3. Upon successfully decoding a JPEG image, pass it to a dispatcher or callback function (e.g., `run_ai(image, camera_id, image_id)`). Provide a dummy stub for the inference pipeline.
4. Ensure network resilience. If a sender disconnects, the server must gracefully close the socket and go back to `accept()` to wait for a reconnection without crashing.
