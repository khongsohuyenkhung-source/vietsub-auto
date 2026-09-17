import os
import subprocess
import tempfile
from io import BytesIO

from flask import Flask, request, render_template_string, send_file
from openai import OpenAI


app = Flask(__name__)

# Giới hạn video upload: 500 MB
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

# Lấy API key từ Render Environment Variables
api_key = os.environ.get("OPENAI_API_KEY")

if not api_key:
    print("WARNING: Chưa có OPENAI_API_KEY")

client = OpenAI(api_key=api_key)


HTML = """
<!DOCTYPE html>
<html lang="vi">

<head>
    <meta charset="UTF-8">

    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">

    <title>Vietsub Auto</title>

    <style>

        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            font-family: Arial, sans-serif;
            background:
                linear-gradient(
                    135deg,
                    #111827,
                    #1e3a8a
                );
            color: white;
            min-height: 100vh;
        }

        .container {
            width: 90%;
            max-width: 800px;
            margin: 60px auto;
        }

        .box {
            background: rgba(17, 24, 39, 0.95);
            padding: 35px;
            border-radius: 20px;
            box-shadow:
                0 20px 60px rgba(0, 0, 0, 0.4);
        }

        h1 {
            text-align: center;
            font-size: 32px;
            margin-bottom: 10px;
        }

        .description {
            text-align: center;
            color: #9ca3af;
            margin-bottom: 30px;
        }

        .upload-area {
            border: 2px dashed #4b5563;
            border-radius: 15px;
            padding: 30px;
            text-align: center;
            margin-bottom: 20px;
        }

        input[type="file"] {
            width: 100%;
            padding: 15px;
            background: #374151;
            color: white;
            border-radius: 10px;
            border: 1px solid #4b5563;
        }

        button {
            width: 100%;
            padding: 16px;
            border: none;
            border-radius: 10px;
            background: #2563eb;
            color: white;
            font-size: 18px;
            font-weight: bold;
            cursor: pointer;
        }

        button:hover {
            background: #1d4ed8;
        }

        .message {
            margin-top: 20px;
            padding: 15px;
            background: #374151;
            border-radius: 10px;
            text-align: center;
        }

        .error {
            color: #fca5a5;
        }

        .info {
            color: #93c5fd;
        }

        .note {
            margin-top: 20px;
            color: #9ca3af;
            font-size: 13px;
            text-align: center;
            line-height: 1.6;
        }

    </style>

</head>


<body>

<div class="container">

    <div class="box">

        <h1>🇨🇳 → 🇻🇳 Vietsub Auto</h1>

        <div class="description">
            Tự nhận diện tiếng Trung và tạo phụ đề tiếng Việt.
        </div>


        <form
            action="/vietsub"
            method="POST"
            enctype="multipart/form-data"
        >

            <div class="upload-area">

                <input
                    type="file"
                    name="video"
                    accept="video/*"
                    required
                >

            </div>


            <button type="submit">
                🚀 BẮT ĐẦU VIETSUB
            </button>

        </form>


        {% if message %}

        <div class="message {{ message_type }}">
            {{ message }}
        </div>

        {% endif %}


        <div class="note">
            Video → Nhận diện tiếng Trung → Dịch tiếng Việt → SRT
        </div>

    </div>

</div>

</body>

</html>
"""


def run_command(command):
    """
    Chạy lệnh hệ thống.
    """

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise Exception(result.stderr)

    return result


def convert_video_to_audio(video_path, audio_path):
    """
    Chuyển video thành WAV:
    - mono
    - 16kHz
    """

    command = [
        "ffmpeg",
        "-y",

        "-i",
        video_path,

        "-vn",

        "-ac",
        "1",

        "-ar",
        "16000",

        "-c:a",
        "pcm_s16le",

        audio_path
    ]

    run_command(command)


def split_audio(audio_path, output_folder):
    """
    Chia audio thành các đoạn 10 phút.

    Mục đích:
    tránh file audio quá lớn khi gửi lên API.
    """

    pattern = os.path.join(
        output_folder,
        "part_%03d.wav"
    )

    command = [
        "ffmpeg",
        "-y",

        "-i",
        audio_path,

        "-f",
        "segment",

        "-segment_time",
        "600",

        "-reset_timestamps",
        "1",

        "-c",
        "copy",

        pattern
    ]

    run_command(command)

    files = []

    for filename in sorted(
        os.listdir(output_folder)
    ):

        if filename.startswith("part_") \
                and filename.endswith(".wav"):

            files.append(
                os.path.join(
                    output_folder,
                    filename
                )
            )

    return files


def transcribe_part(audio_file):
    """
    Nhận diện tiếng Trung.

    whisper-1 trả về segment có timestamp.
    """

    with open(audio_file, "rb") as f:

        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="zh",
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )

    return result.segments


def translate_text(text):
    """
    Trung -> Việt.
    """

    response = client.responses.create(

        model="gpt-5.6-luna",

        instructions=(
            "Bạn là biên dịch viên phụ đề phim Trung Quốc "
            "sang tiếng Việt. "

            "Dịch trung thành với nguyên nghĩa. "
            "Ưu tiên câu tiếng Việt tự nhiên nhưng không "
            "tự ý thêm hoặc bớt nội dung. "

            "Giữ đúng sắc thái hội thoại. "
            "Tên người, địa danh và thuật ngữ riêng "
            "phải được xử lý nhất quán. "

            "Chỉ trả về bản dịch. "
            "Không giải thích."
        ),

        input=text
    )

    return response.output_text.strip()


def format_time(seconds):
    """
    Giây -> HH:MM:SS,mmm
    """

    total_seconds = int(seconds)

    milliseconds = int(
        round(
            (seconds - total_seconds) * 1000
        )
    )

    if milliseconds >= 1000:

        total_seconds += 1
        milliseconds = 0

    hours = total_seconds // 3600

    minutes = (
        total_seconds % 3600
    ) // 60

    secs = total_seconds % 60

    return (
        f"{hours:02}:"
        f"{minutes:02}:"
        f"{secs:02},"
        f"{milliseconds:03}"
    )


def create_srt(all_segments):
    """
    Tạo file SRT.
    """

    output = []

    subtitle_number = 1

    for segment in all_segments:

        text = segment["text"].strip()

        if not text:
            continue

        start = format_time(
            segment["start"]
        )

        end = format_time(
            segment["end"]
        )

        print(
            f"Dịch câu {subtitle_number}: "
            f"{text}"
        )

        vietnamese = translate_text(text)

        output.append(
            str(subtitle_number)
        )

        output.append(
            f"{start} --> {end}"
        )

        output.append(
            vietnamese
        )

        output.append("")

        subtitle_number += 1

    return "\n".join(output)


@app.route("/")
def home():

    return render_template_string(
        HTML
    )


@app.route(
    "/vietsub",
    methods=["POST"]
)
def vietsub():

    video = request.files.get(
        "video"
    )

    if not video:

        return render_template_string(
            HTML,
            message="Bạn chưa chọn video.",
            message_type="error"
        )

    if video.filename == "":

        return render_template_string(
            HTML,
            message="Tên video không hợp lệ.",
            message_type="error"
        )

    if not api_key:

        return render_template_string(
            HTML,
            message=(
                "Server chưa được cấu hình "
                "OPENAI_API_KEY."
            ),
            message_type="error"
        )


    try:

        with tempfile.TemporaryDirectory() as temp:

            video_path = os.path.join(
                temp,
                "video"
            )

            audio_path = os.path.join(
                temp,
                "audio.wav"
            )

            parts_folder = os.path.join(
                temp,
                "parts"
            )

            os.makedirs(
                parts_folder,
                exist_ok=True
            )


            # ==================================
            # 1. Lưu video
            # ==================================

            video.save(
                video_path
            )


            # ==================================
            # 2. Video -> WAV
            # ==================================

            print(
                "Đang chuyển video thành audio..."
            )

            convert_video_to_audio(
                video_path,
                audio_path
            )


            # ==================================
            # 3. Chia audio
            # ==================================

            print(
                "Đang chia audio..."
            )

            audio_parts = split_audio(
                audio_path,
                parts_folder
            )


            if not audio_parts:

                raise Exception(
                    "Không tạo được audio."
                )


            # ==================================
            # 4. Nhận diện tiếng Trung
            # ==================================

            all_segments = []

            offset = 0

            for part in audio_parts:

                print(
                    f"Đang nhận diện: {part}"
                )

                segments = transcribe_part(
                    part
                )


                for segment in segments:

                    all_segments.append(
                        {
                            "start":
                                segment.start + offset,

                            "end":
                                segment.end + offset,

                            "text":
                                segment.text
                        }
                    )


                # Mỗi part dài khoảng 600 giây
                offset += 600


            if not all_segments:

                raise Exception(
                    "Không nhận diện được "
                    "tiếng nói trong video."
                )


            # ==================================
            # 5. Trung -> Việt
            # ==================================

            print(
                "Bắt đầu dịch Trung -> Việt..."
            )

            srt_content = create_srt(
                all_segments
            )


            # ==================================
            # 6. Trả file SRT
            # ==================================

            return send_file(

                BytesIO(
                    srt_content.encode(
                        "utf-8"
                    )
                ),

                as_attachment=True,

                download_name=(
                    "vietsub-viet.srt"
                ),

                mimetype=(
                    "application/x-subrip"
                )
            )


    except Exception as e:

        print(
            "ERROR:",
            str(e)
        )

        return render_template_string(

            HTML,

            message=(
                "Có lỗi xảy ra: "
                + str(e)
            ),

            message_type="error"
        )


@app.errorhandler(413)
def file_too_large(error):

    return render_template_string(

        HTML,

        message=(
            "Video quá lớn. "
            "Giới hạn hiện tại là 500 MB."
        ),

        message_type="error"
    )


@app.route("/health")
def health():

    return {
        "status": "ok"
    }


if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
