# WF51 Pi Zero 2W Camera Sender Service

ระบบรวบรวมและส่งภาพดิบจากกล้อง Raspberry Pi Zero 2W ไปยัง AI Inference Server ผ่าน TCP/IP ออกแบบมาให้กินทรัพยากรต่ำสุดเหมาะสำหรับ Pi Zero 2W โดยทำการส่งภาพแบบ Raw Image โดยไม่ทำการ Pre-processing ใดๆ ภายในบอร์ด

## 📂 โครงสร้างโฟลเดอร์ (Directory Structure)

```text
/home/wf51/pi02w_sender/
├── README.md                       # คู่มือฉบับนี้
├── mock/                           # โฟลเดอร์เก็บภาพจำลองสำหรับทดสอบออฟไลน์
│
└── camera_node/
    ├── main.py                     # สคริปต์หลัก (Main Camera Node) - สำหรับส่ง Raw Image
    ├── setup.sh                    # สคริปต์ติดตั้ง Systemd Service  
    ├── camera-sender@.service      # Systemd Template Service
    ├── requirements.txt            # Python Dependencies
    │
    ├── configs/                    # ไฟล์ตั้งค่าของระบบ
    │   └── config_cam0.json        # ตั้งค่ากล้องตัวหลัก (TCP Port 8080)
    │
    └── logs/                       # โฟลเดอร์เก็บรูปบันทึก (ถ้ามี)
```

---

## ⚙️ 1. การตั้งค่าระบบ (Configuration)

ไฟล์ Config อยู่ที่ `configs/config_cam0.json` แบ่งออกเป็น 3 หมวดหลัก:

### `tcp` – ปลายทางรับภาพ (AI Inference Server)
| Key | คำอธิบาย |
|---|---|
| `ip` | IP ของ Receiver (เช่น `10.10.10.199` หรือ `wfmain.local`) |
| `port` | `8080` สำหรับ cam0 |

### `mqtt` – การตั้งค่า MQTT Broker สำหรับควบคุมและส่งสถานะ
| Key | คำอธิบาย |
|---|---|
| `broker` | IP หรือ Host ของ MQTT Broker (เช่น `10.10.10.199`) |
| `port` | Port ของ MQTT Broker (ค่าเริ่มต้น `1883`) |
| `username` | ชื่อผู้ใช้สำหรับเชื่อมต่อ MQTT (ถ้ามี) |
| `password` | รหัสผ่านสำหรับเชื่อมต่อ MQTT (ถ้ามี) |
| `topic_cmd` | Topic สำหรับรับคำสั่งควบคุมกล้อง (เช่น `camera/command`) |
| `topic_status` | Topic สำหรับส่งสถานะของตัวเครื่อง (เช่น `camera0/status` หรือ `camera1/status`) |

### `camera` – พฤติกรรมการถ่ายภาพและการตั้งค่ากล้อง
| Key | คำอธิบาย |
|---|---|
| `id` | หมายเลข Hardware Camera (`0` หรือ `1`) |
| `default_width` | ความกว้างเริ่มต้นของภาพ (เช่น `2304`) |
| `default_height`| ความสูงเริ่มต้นของภาพ (เช่น `1296`) |
| `jpeg_quality` | คุณภาพการบีบอัด JPEG `1-100` (แนะนำ `90`) |
| `continuous_stream` | `false` = รอคำสั่ง MQTT, `true` = ส่งอัตโนมัติตาม `stream_interval` |
| `stream_interval` | ความเร็วในการส่งภาพ (วินาที) ใช้เมื่อ `continuous_stream: true` |
| `loop_delay` | ระยะเวลาหน่วงใน Main Loop เพื่อเซฟการทำงาน CPU (วินาที) |

*(หมวด Preprocessing ถูกปิดใช้งานเพื่อรักษาประสิทธิภาพของ Pi Zero 2W และส่งแต่ภาพดิบส่งให้ Server หลักไปประมวลผลต่อ)*

---

## 🚀 2. การวิ่งระบบในโหมดจริง

ระบบใช้ **Systemd Template Service (`camera-sender@.service`)** ซึ่งจะรันรหัสกล้อง `@0` แยกเป็นอิสระ

### ติดตั้ง Service ครั้งแรก:
```bash
cd ~/pi02w_sender/camera_node
chmod +x setup.sh
./setup.sh
```

### คำสั่งจัดการ Service:
| คำสั่ง | ผล |
|---|---|
| `sudo systemctl start camera-sender@0` | เริ่มทำงานกล้อง 0 |
| `sudo systemctl stop camera-sender@0` | หยุดการทำงานกล้อง 0 |
| `sudo systemctl status camera-sender@0` | ดูสถานะและเช็ค error |
| `journalctl -u camera-sender@0 -f` | ดู Log แบบ Real-time |

---

## 🔔 3. การควบคุมและส่งสถานะผ่าน MQTT (IoT Telemetry)

### การรับคำสั่ง (MQTT Command Topic)
การลั่นชัตเตอร์แบบ Manual เป็นวิธีที่แนะนำหากต้องการรับรองว่า **ภาพทั้ง 2 กล้องมาจากชิ้นงานเดียวกันเสมอ**

ระบบจะฟังคำสั่งจาก Topic ที่ตั้งไว้ใน `topic_cmd` (เช่น `camera/command`)
เมื่อส่งคำสั่งนี้เข้าไป กล้องจะได้รับคำสั่งและทำงานทันที:

```json
{ "action": "capture" }
```

คำสั่งสั่งการผ่าน MQTT ที่รองรับในปัจจุบัน:

| Payload | ผล |
|---|---|
| `{"action": "capture"}` | ลั่นชัตเตอร์ถ่ายรูปและส่ง 1 ชุด |
| `{"ExposureTime": 10000}` | ปรับ Exposure Time (µs) |
| `{"AnalogueGain": 2.0}` | ปรับ Gain |
| `{"resolution": [1920, 1080]}` | ปรับเปลี่ยนความละเอียดกล้องแบบ Real-time |
| `{"system": "restart"}` | สั่ง Reboot บอร์ด |
| `{"system": "shutdown"}`| สั่ง Shutdown บอร์ด |

### การส่งสถานะการทำงาน (MQTT Status Topic)
โปรแกรมถูกพัฒนาให้รายงานสถานะตัวเครื่องตลอดการทำงาน ทุกๆ 5 วินาที โดยจะส่งไปยัง Topic ที่ระบุใน `topic_status` (ตาม hostname เครื่อง เช่น `wf51/status`) ข้อมูลเป็นดังนี้:

```json
{
  "cpu_temp": 48.5,
  "ram_usage_percent": 15.6,
  "cpu_usage_percent": 8.0,
  "resolution": [2304, 1296]
}
```

---

## 🛠️ 4. การทดสอบออฟไลน์ (Mock Mode)

ทดสอบการส่งผ่าน TCP/IP ได้โดยไม่ต้องมีกล้องจริง โดยใช้ภาพในโฟลเดอร์ `mock/`:

### รัน Mock พื้นฐาน:
```bash
cd ~/pi02w_sender/camera_node
python3 main.py -c configs/config_cam0.json --mock_dir ~/pi02w_sender/mock
```

---

## 📡 5. โครงสร้างโปรโตคอลการส่งข้อมูล (TCP Stream Protocol)

ในโหมด Pi Zero 2W ระบบจะส่งภาพดิบรูปแบบ 1 ชัตเตอร์ = 1 ภาพ โดยห่อด้วยโปรโตคอล 3 ส่วน:

| ลำดับ | ขนาด | รูปแบบ | คำอธิบาย |
|---|---|---|---|
| 1 | 4 bytes | Big-endian `>L` | ขนาดของ JSON Metadata ที่จะตามมา |
| 2 | N bytes (จากข้อ 1) | UTF-8 JSON String | Metadata เช่น `{"id": "raw_image", "size": 125134}` |
| 3 | M bytes (จาก `size`) | Raw JPEG | ไฟล์ภาพ JPEG พร้อมใช้ |

> 📎 โปรโตคอลนี้ออกแบบมาให้ Receiver สามารถรับภาพและแยกส่วน Metadata ได้อย่างรวดเร็ว โดยอาศัยขนาดที่ระบุไว้ชัดเจนเสมอ
