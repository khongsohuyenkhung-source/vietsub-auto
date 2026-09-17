import os
import subprocess
import tempfile
from flask import Flask, request, render_template_string, send_file

from openai import OpenAI

app = Flask(__name__)

# Giới hạn video upload
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024  # 300 MB

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY")
)

HTML = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Vietsub Auto</title>

    <style>
        body {
            margin: 0;
            font-family: Arial, sans-serif;
            background: #111827;
            color: white;
        }

        .container {
            max-width: 800px;
            margin: 60px auto;
            padding: 20px;
        }

        .box {
            background: #1f2937;
            padding: 30px;
            border-radius: 18px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.3);
        }

        h1 {
            text-align: center;
            margin-bottom: 10px;
        }

        .desc {
            text-align: center;
            color: #9ca3af;
            margin-bottom: 30px;
        }

        input[type=file] {
            width: 100%;
            padding: 15px;
            box-sizing: border-box;
            background: #374151;
            color: white;
            border-radius: 10px;
            border: 1px solid #4b5563;
            margin-bottom: 20px;
        }

        button {
            width: 100%;
            padding: 15px;
            border: none;
            border-radius: 10px;
            background: #2563eb;
            color: white;
            font-size: 17px;
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

        .success {
            color: #86efac;
        }
    </style>
</head>

<body>

<div class="container">

    <div class="box">

        <h1>🇨🇳 → 🇻🇳 Vietsub Auto</h1>

        <div class="desc">
            Upload video tiếng Trung và hệ thống tự tạo phụ đề tiếng Việt.
        </div>

        <form action="/vietsub" method="POST" enctype="multipart/form-data">

            <input
                type="file"
                name="video"
                accept="video/*"
                required
            >

            <button type="submit">
                🚀 Bắt đầu Vietsub
            </button>

        </form>

        {% if message %}
            <div class="message {{ message_type }}">
                {{ message }}
            </div>
        {% endif %}

    </div>

</div>

</body>
</html>
"""


def convert_to_audio(video_path, audio_path):
    """
    Chuyển video thành audio WAV:
    mono / 16kHz để nhận diện giọng nói tốt hơn.
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

    subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True
    )


def transcribe_audio(audio_path):
    """
    Nhận diện tiếng Trung và lấy timestamp theo từng đoạn.
    """

    with open(audio_path, "rb") as audio_file:

        result = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )

    return result.segments


def translate_text(chinese_text):
    """
    Dịch Trung -> Việt.
    """

    response = client.responses.create(
        model="gpt-5.6-luna",
        input=[
            {
                "role": "system",
                "content": (
                    "Bạn là biên dịch viên phụ đề phim Trung Quốc sang tiếng Việt. "
                    "Hãy dịch trung thành với nguyên nghĩa, tự nhiên trong tiếng Việt, "
                    "giữ đúng ngữ cảnh hội thoại. "
                    "Không giải thích. Không thêm nội dung. "
                    "Không dịch tên riêng nếu tên đó nên được giữ nguyên."
                )
            },
            {
                "role": "user",
                "content": chinese_text
            }
        ]
    )

    return response.output_text.strip()


def format_time(seconds):
    """
    Chuyển giây thành định dạng SRT:
    HH:MM:SS,mmm
    """

    milliseconds = int((seconds - int(seconds)) * 1000)

    total_seconds = int(seconds)

    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60

    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"


def create_srt(segments):

    lines = []

    for index, segment in enumerate(segments, start=1):

        chinese = segment.text.strip()

        if not chinese:
            continue

        print(
            f"Dịch câu {index}: {chinese}"
        )

        vietnamese = translate_text(chinese)

        start = format_time(segment.start)
        end = format_time(segment.end)

        lines.append(str(index))
        lines.append(f"{start} --> {end}")
        lines.append(vietnamese)
        lines.append("")

    return "\n".join(lines)


@app.route("/")
def home():

    return render_template_string(
        HTML,
        message=None,
        message_type=""
    )


@app.route("/vietsub", methods=["POST"])
def vietsub():

    video = request.files.get("video")

    if not video:

        return render_template_string(
            HTML,
            message="Chưa chọn video.",
            message_type="error"
        )

    if video.filename == "":

        return render_template_string(
            HTML,
            message="Tên video không hợp lệ.",
            message_type="error"
        )

    try:

        with tempfile.TemporaryDirectory() as temp_dir:

            video_path = os.path.join(
                temp_dir,
                "video"
            )

            audio_path = os.path.join(
                temp_dir,
                "audio.wav"
            )

            srt_path = os.path.join(
                temp_dir,
                "vietsub.srt"
            )

            # Lưu video
            video.save(video_path)

            # 1. Video -> audio
            convert_to_audio(
                video_path,
                audio_path
            )

            # 2. Audio -> tiếng Trung + timestamp
            segments = transcribe_audio(
                audio_path
            )

            if not segments:

                return render_template_string(
                    HTML,
                    message="Không nhận diện được giọng nói.",
                    message_type="error"
                )

            # 3. Trung -> Việt
            srt_content = create_srt(
                segments
            )

            # 4. Lưu SRT
            with open(
                srt_path,
                "w",
                encoding="utf-8"
            ) as f:

                f.write(srt_content)

            # Đọc vào bộ nhớ trước khi TemporaryDirectory bị xóa
            srt_data = srt_content.encode("utf-8")

            from io import BytesIO

            return send_file(
                BytesIO(srt_data),
                as_attachment=True,
                download_name="vietsub-viet.srt",
                mimetype="text/plain; charset=utf-8"
            )

    except Exception as e:

        print("ERROR:", e)

        return render_template_string(
            HTML,
            message=f"Lỗi: {str(e)}",
            message_type="error"
        )


@app.errorhandler(413)
def too_large(error):

    return render_template_string(
        HTML,
        message="Video quá lớn. Hiện tại giới hạn là 300 MB.",
        message_type="error"
    )


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
