# UNO Q Adreno 板上合成計畫

目標：用現有的 Niko 線稿和 58 個區域，在 UNO Q 上以 Adreno 702 合成透明角色圖。保留 Windows 的網頁介面、OpenCV 分區和雲端判色。比較原有的瀏覽器合成與透過 USB 交給板子的整體時間。

## 分工

- **Windows**：切分線稿、保留區號和筆觸，接收模型回傳的底色、處理手動修改及背景預覽。雲端判色仍從所選角色參考圖推斷固有底色；不送預設色票，也不把 API 金鑰存到 UNO Q。
- **UNO Q**：用 EGL 建立不需螢幕的 OpenGL ES 3 繪圖環境。新分區先傳一份灰階線稿和像素區號；之後同一分區只傳「區號 → RGBA」對照。GPU 查色、保留線條深淺及透明區，板上輸出 PNG。合成參數來自已選定的顏色，與提供給模型的參考圖分開。
- **接線**：用 USB ADB 轉發普通 HTTP 連線，兩端服務均綁定 loopback。Windows 預設可獨立運作。UI 可切換合成位置，顯示實際結果來自哪裡；板子不可用時讓瀏覽器接手。

## 實作與驗收

- [x] 確認 USB 可讀取 UNO Q，板上有 `/dev/dri/renderD128`、Mesa EGL / GLES 動態函式庫，使用者有 render 群組權限。
- [x] 實作離屏渲染和板端服務。實機回報 `FD702`、OpenGL ES 3.1；6 像素測試通過色值、筆觸、透明度與上下方向。
- [x] 在 Windows 增加板端轉接和 UI 選項。第一次傳線稿與區號圖，後續只傳顏色；重新分區會換一份遮罩，過期的合成結果不覆蓋新預覽。
- [x] 用 Niko 驗收。仍有 58 區；兩條路徑的 3000 × 3900 透明 PNG 在 1170 萬個像素的 RGBA 完全一致。驗證試色、PNG 下載、重新分區、舊版本拒絕及連線錯誤時切回瀏覽器。
- [x] 實測首次及後續改色，記錄板上與往返耗時。保留 Windows 合成為預設，UNO Q 可在畫布上方勾選。

目前只做平塗和原筆觸合成。已接受的分區不調整；主圓與弧形的燈泡分界、光影層及本地模型另外處理。

## 實測結果

2026-11-18，以原尺寸 `niko_sketch.jpg` 和固定的手動測試色在這台 Windows 與 USB 連接的 UNO Q 上測試。確認板上為 `FD702`、OpenGL ES 3.1（Mesa 25.2.6）。未呼叫雲端 API，也未將測試色傳給模型。

| 路徑 | 操作到預覽顯示 |
| --- | ---: |
| Windows 瀏覽器合成，7 次改色中位數 | 170 ms |
| UNO Q 首次合成，含壓縮和上傳分區圖 | 1,470 ms |
| UNO Q 後續改色，7 次中位數 | 1,000 ms |

後續改色只送 1 KiB 色表。板上繪製約 126 ms，像素讀回約 318 ms，PNG 編碼約 415 ms；USB 往返與 HTTP 其餘時間約 47 ms（各階段取中位數，不含瀏覽器解碼與顯示）。目前每次都回傳整張原尺寸 PNG，讀回及編碼是最大的成本。Windows 適合反覆試色；UNO Q 可用來驗證板上渲染與輸出。未來若要做板上的即時預覽，可以只回傳縮圖，下載時才輸出全解析度。

- [桌面驗收畫面](../.debug/screenshots/niko-adreno-board.png)和[窄版驗收畫面](../.debug/screenshots/niko-adreno-mobile.png)。
- [板上輸出](../.debug/niko-adreno-board.png)、[Windows 輸出](../.debug/niko-adreno-windows.png)及[測時原始資料](../.debug/unoq-adreno-timings.json)。
- [重新啟動方式](README.md#用-uno-q-合成)。
