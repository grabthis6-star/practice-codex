# MP4 하드자막 OCR Flask 웹앱

업로드한 MP4 영상에서 **음성 전사 없이 화면 하드자막만 OCR**로 추출합니다.
- Whisper / YouTube 자막 API 사용 안 함
- OpenCV로 프레임 추출 + ROI 크롭
- pytesseract(`kor+eng`) OCR

## 기능

1. MP4 업로드
2. 0s, 5s, 10s, 20s, 30s, 40s 기준으로 최대 6장 썸네일 자동 추출
3. 자막이 보이는 프레임 선택
4. 선택 프레임에서 마우스 드래그로 ROI(자막 영역) 지정
5. 1초 간격으로 프레임 추출 후 ROI만 OCR
6. 중복/유사 문장 제거 후 결과 표시
7. TXT 다운로드
8. OCR 도중/후 "ROI 재설정" 가능

## 파일 구조

- `app.py`
- `templates/index.html`
- `static/style.css`
- `static/app.js`
- `requirements.txt`

## 설치

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Windows에서 Tesseract + 한국어 언어팩 설치 (중요)

`pytesseract`는 OCR 엔진(Tesseract)이 별도 설치되어 있어야 동작합니다.

### 1) Tesseract 설치

1. 아래 프로젝트에서 Windows 설치 파일(예: `tesseract-ocr-w64-setup-*.exe`) 다운로드
   - https://github.com/UB-Mannheim/tesseract/wiki
2. 설치 경로 예시: `C:\Program Files\Tesseract-OCR`
3. 설치 중 **Additional language data** 항목에서 Korean(`kor`) 체크

### 2) `kor.traineddata` 확인

아래 파일이 존재하는지 확인:

```text
C:\Program Files\Tesseract-OCR\tessdata\kor.traineddata
```

없으면 아래 저장소에서 `kor.traineddata`를 받아 `tessdata` 폴더에 복사:
- https://github.com/tesseract-ocr/tessdata

### 3) PATH 설정

시스템 환경변수 Path에 아래 추가:

```text
C:\Program Files\Tesseract-OCR
```

적용 후 새 터미널에서 확인:

```bash
tesseract --version
```

### 4) (필요 시) app.py에서 경로 강제 지정

PATH 인식이 안 되는 경우 `app.py` 상단에 아래를 추가할 수 있습니다.

```python
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
```

## 실행

```bash
python app.py
```

브라우저에서 `http://localhost:5000` 접속.

## 사용 팁

- 자막 영역 ROI는 자막 줄을 충분히 포함하되 불필요한 배경을 줄이면 정확도가 올라갑니다.
- 영상 해상도나 글꼴에 따라 인식 품질이 달라질 수 있습니다.
- OCR 중 "ROI 재설정"을 누르면 현재 작업을 취소하고 ROI를 다시 잡을 수 있습니다.
