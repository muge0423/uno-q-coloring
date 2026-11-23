# 線稿填色台（本機原型）

這個原型用本機 OpenCV 將手繪線稿分成可點選區域，可手動配色，也能讓 GPT 依角色參考圖推斷底色，再由本機按原遮罩填色。

## 啟動

需求：Python、OpenCV、NumPy。若目前環境沒有安裝：

```powershell
python -m pip install opencv-python numpy
```

解壓縮原始碼，進入有 `server.py` 的資料夾，執行：

```powershell
python .\server.py
```

開啟 `http://127.0.0.1:8765`，選取自己的 PNG 或 JPEG 線稿即可手動上色。按 `Ctrl+C` 停止伺服器。也可用 `start_localhost.bat` 啟動並開啟網頁。

若備有線稿，可用 `--sample` 指定路徑，例如 `python .\server.py --sample ".\my_sketch.jpg"`。原始碼壓縮檔未附展示圖。

## 用參考圖自動上底色

手動填色不需要金鑰。使用 AI 時，展開左側「API 連線設定」：

1. 輸入服務的 API Key 與 API 請求位址（基礎網址，以 `/v1` 結尾；不要加 `/responses` 或 `/chat/completions`）。「帶入 Railway 範例網址」會填入 `https://asiasouth.up.railway.app/v1`，請搭配該服務發給您的金鑰。
2. 選擇服務支援的 API 介面。OpenAI 預設使用 Responses；若中繼服務只有 Chat Completions，切換至後者。回覆格式預設為 JSON Schema；服務若不支援，可改為 JSON 物件或純文字 JSON。系統會驗證回傳的區號與色值。
3. 按「取得模型清單」，或直接在模型 ID 欄位輸入，再按「儲存設定」。清單中不一定每個模型都能讀圖，請確認選用的模型支援圖片輸入。按「測試圖片判讀」可用本機產生的合成色塊確認圖片請求與回覆格式；這項測試不會傳送線稿或角色參考圖。

取得模型清單時會先儲存設定並連線至指定服務；按「AI 上底色」時才會上傳圖片。金鑰只保存在本機伺服器程序記憶體，不寫入檔案；畫面留空並儲存會保留目前金鑰，按「清除已儲存金鑰」會清除本次工作階段的金鑰。若使用 `OPENAI_API_KEY` 環境變數，重啟伺服器會重新讀取它。更換 API 網址時必須輸入新服務的金鑰或先清除舊金鑰。遠端服務需使用 HTTPS，本機測試可用 HTTP。請確認中繼服務的資料處理與收費方式。

也可沿用環境變數 `OPENAI_API_KEY` 與 `OPENAI_MODEL`。例如下方指令會隱藏金鑰輸入，只在目前程序保留金鑰：

```powershell
$env:OPENAI_API_KEY = ([pscredential]::new('OpenAI', (Read-Host 'OpenAI API key' -AsSecureString))).GetNetworkCredential().Password
python .\server.py
```

若原本的 `8765` 還在使用，可在啟動指令加上 `--port 8766`，並開啟 `http://127.0.0.1:8766`。

若以環境變數提供金鑰，伺服器重啟時會再讀取該環境變數；可在網頁輸入其他金鑰覆蓋該次工作階段。預設模型是 `gpt-5.4-mini`，仍可在網頁或以 `OPENAI_MODEL` 改成您服務提供的模型。

1. 載入線稿，確認分區後，在「角色參考圖」加入 1 至 3 張圖片（PNG、JPEG 或 WebP，單張最多 8 MB）。第一張是主要參考，建議用光影較少的角色設定圖。
2. 按「AI 上底色」。本機伺服器會把參考圖、灰階線稿、帶編號的分區圖及小區塊近照送至設定的 API 服務。選圖時不會呼叫雲端 API；內建色票、手動上色結果及背景預覽圖都不會送出。
3. 模型回傳自己推斷的底色色值、每種顏色的區號、背景區與待確認區。本機依回傳套色，已手動調整的區塊會保留。用「只看待確認」篩選還需要手動處理的區塊。

## 用 UNO Q 合成

在畫布上方勾選「UNO Q 合成」，切到「填色預覽」。看到 `合成：UNO Q Adreno · FD702` 時，透明 PNG 是板子用 Adreno 合成的。取消勾選就回到瀏覽器合成；板子無法連線時亦會自動使用瀏覽器。Windows 的分區和雲端判色照常；板子只接收本機分區圖、灰階線稿和已選定的顏色。

重啟時，用 USB 連接板子。可在上述解壓縮資料夾執行 `start_unoq.cmd`，保留該視窗後再執行 `start_localhost.bat`。也可在同一資料夾開啟 PowerShell，按以下步驟啟動板端服務。如果 PATH 裡沒有 `adb`，下例使用這台 Windows 已安裝的 Arduino 工具。可先以 `& $adb devices -l` 確認連線；同時連了其他裝置時，在後續 ADB 命令加 `-s <UNO Q 裝置序號>`。

```powershell
$adb = (Get-Command adb -ErrorAction SilentlyContinue).Source
if (-not $adb) { $adb = Join-Path $env:LOCALAPPDATA 'Arduino15\packages\arduino\tools\adb\32.0.0\adb.exe' }
$boardDir = (& $adb shell 'mktemp -d /tmp/uno-q-color.XXXXXX').Trim()
& $adb push .\adreno.py "$boardDir/adreno.py"
& $adb push .\unoq_renderer.py "$boardDir/unoq_renderer.py"
& $adb shell -t "python3 $boardDir/unoq_renderer.py --port 18765"
```

看到 `FD702` 後保留該終端機。在同一資料夾另開 PowerShell，做 USB 轉發並啟動上色台：

```powershell
$adb = (Get-Command adb -ErrorAction SilentlyContinue).Source
if (-not $adb) { $adb = Join-Path $env:LOCALAPPDATA 'Arduino15\packages\arduino\tools\adb\32.0.0\adb.exe' }
& $adb forward --no-rebind tcp:18765 tcp:18765
python .\server.py --unoq-render-port 18765
```

轉發若已存在，可用 `& $adb forward --list` 確認 `tcp:18765` 仍指向這塊 UNO Q。板端只需要 Python 3、Mesa EGL 和 OpenGL ES；不需要安裝 OpenCV 或模型。板上合成目前支援最多 1600 萬像素。實測結果和分工見 [Adreno 板上合成計畫](<Adreno 板上合成計畫.md>)。

## 目前範圍

- 調整線條閾值與自動補小缺口。
- 點選區塊套用手動色票或模型回傳的底色，也可指定任意顏色。
- 手動畫補線後重算分區；補線只影響遮罩，不會畫入輸出。
- 指定透明區、預覽背景，輸出透明 PNG 或合成圖。
- 放大／縮小畫布（75–300%）；放大時只標示目前視窗內的區域，標籤以引線避讓，捲動畫布會同步更新。從右側區域清單選一列會依區域大小放大並定位；小於 1,500 px 的區域會自動放大至 300%，方便檢查細小邊角。
- 分區由 Windows 完成；平塗可用瀏覽器或 UNO Q 合成。模型不會改動線稿或分區邊界。

Niko 範例的圓形光球是獨立物件；左上相鄰的兩段弧形不是光球。預設連通區目前仍會把其中一段弧形併入主圓遮罩，這項分界修正先列為待辦，確認遮罩前不要把目前預覽當成正確光球成品。`niko_fixed.jpg` 用來驗證姿勢與遮罩，不作為固有底色色票來源，因為其中有明顯的場景光影。
