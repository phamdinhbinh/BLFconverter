# BLF Signal Converter

Ứng dụng desktop cho **Windows 10/11 64 bit**, chuyển BLF + DBC thành **MF4 (MDF 4.10)** và **Excel XLSX**. Thiết kế cho log lớn, gồm CAN và CAN FD đến 64 byte.

## Chạy trên Windows

1. Giải nén toàn bộ thư mục vào ổ SSD.
2. Cài **Python 3.12 64 bit** từ python.org. Giữ thành phần **Tcl/Tk** và **Python Launcher** khi cài.
3. Nhấp đúp **Run_Windows.bat**. Lần đầu script tạo môi trường Python riêng và tải thư viện. Những lần sau mở ứng dụng trực tiếp, không cần mạng để chuyển đổi.
4. Thêm một hoặc nhiều BLF; thêm DBC; chọn thư mục kết quả và nhấn **Bắt đầu chuyển đổi**.

Nếu có nhiều bus dùng trùng ID, chọn đúng **CAN 1, CAN 2, …** trước khi thêm từng DBC. Các kênh đánh số từ **1**, trùng với số kênh lưu trong BLF. DBC gán cho một kênh cụ thể có ưu tiên cao hơn DBC gán cho “Tất cả”. DBC có ID trùng nhau trong cùng phạm vi sẽ báo lỗi để tránh giải mã nhầm.

**Để chuyển nhanh nhất:** chọn riêng MF4, giữ nén `0`, khối `32 MB`, đọc/ghi trên SSD cục bộ. Bật Excel khi cần bản lưu trữ. Khối `64` hoặc `128 MB` có thể giảm chi phí lặp khi máy còn nhiều RAM; đây là kích thước lô frame, **không phải giới hạn tổng RAM**. Số tín hiệu trong DBC cũng ảnh hưởng RAM và tốc độ.

## File kết quả

| File | Nội dung |
| --- | --- |
| `ten_log.mf4` | Tín hiệu vật lý đã giải mã, đơn vị DBC, thời gian gốc, MDF 4.10 |
| `ten_log_001.xlsx`, `_002.xlsx`, … | Dữ liệu lưu trữ, tự chia workbook/sheet |
| `ten_log.report.json` | Thời gian từng giai đoạn, số frame khớp/không khớp DBC, ID chưa giải mã, cảnh báo và cấu hình |

Mỗi sheet dữ liệu lưu `Time [s]` và một nhóm tín hiệu có chung thời gian lấy mẫu. Sheet **Index** lưu ánh xạ tên tín hiệu, đơn vị, CAN ID, kênh và nguồn DBC. Tín hiệu multiplex chỉ có mẫu ở những frame nhánh đó hoạt động. Không đồng bộ lại toàn bộ tín hiệu thành một bảng, không nội suy, không giảm mẫu.

Excel giới hạn **1.048.576 dòng/sheet**; tool tự chia sheet, đồng thời chia workbook ở khoảng **8 triệu ô dữ liệu** để thuận tiện lưu trữ/mở file. Một BLF 3 GB có thể tạo ra **nhiều** workbook. Số nguyên có hơn 15 chữ số được ghi dạng **text** để giữ chính xác bộ đếm 64 bit không đổi scale/offset. Tín hiệu có scale/offset được tính bằng float64, có thể làm tròn số nguyên rất lớn. Số thực còn chịu giới hạn độ chính xác của Excel; NaN/Infinity ghi ô trống và được giải thích trong Index. MF4 giữ kiểu số của tín hiệu sau giải mã.

File đã tồn tại được giữ nguyên; lần chạy tiếp dùng hậu tố mới. File dở dang nằm trong thư mục tạm tại thư mục đầu ra và được dọn khi lỗi hoặc hủy. Nếu máy mất điện/tiến trình bị tắt cưỡng bức, có thể còn thư mục `.<ten>_work_*` hoặc file `.conversion.lock`; có thể xóa chúng sau khi xác nhận ứng dụng đã đóng.

## Tối ưu tốc độ

- **Đọc BLF trực tiếp theo luồng:** giải nén container và gom lô frame; tránh tạo đối tượng `can.Message` cho từng frame.
- **DBC nạp và biên dịch một lần cho mỗi lượt chuyển:** kế hoạch trích bit được tái sử dụng; Intel/Motorola, signed, scale/offset, IEEE float và multiplex dùng phép toán mảng **NumPy**, không gọi `decode_message()` từng frame.
- **MF4 ghi tăng dần:** `asammdf.append/extend` dùng chung timebase trong từng nhóm. Mặc định không nén, tránh thêm tải CPU và chọn MDF 4.10 thay vì các tính năng MDF mới hơn.
- **Excel ghi XML theo luồng:** không dựng DataFrame hoặc workbook trong RAM; bộ đệm nhỏ, đóng gói ZIP64 ở mức nén 1. Tránh vòng gọi API worksheet cho từng ô.
- **Một lần đọc/giải mã cho hai đầu ra:** không đọc lại BLF riêng cho Excel; không tạo ma trận resample toàn bộ log.
- **Giao diện chạy trên luồng riêng:** cập nhật tiến độ được giới hạn tần suất. Xử lý tuần tự các BLF để RAM không nhân theo số file.

Dung lượng file BLF nén không phản ánh số mẫu sau giải mã. Hãy chừa đủ ổ đĩa cho MF4, XLSX và dữ liệu tạm: có thể lớn hơn BLF nhiều lần. Ghi Excel thường là phần tốn thời gian nhất với DBC nhiều tín hiệu.

## MDA 8.6 và phạm vi dữ liệu

MF4 dùng **MDF 4.10**, các kênh số đã chuyển thành giá trị vật lý, có đơn vị và timestamp tương đối với thời gian bắt đầu log. Tên kênh dạng `CAN1.Message.Signal`. Tool kiểm thử việc mở lại MF4 bằng `asammdf`, so sánh mẫu và timestamp với bộ đọc/giải mã độc lập.

**Chưa xác nhận trực tiếp bằng ETAS MDA 8.6 trong môi trường phát triển này.** Trước khi dùng cho quy trình chính thức, mở một MF4 nhỏ trên MDA 8.6, kiểm tra vài tín hiệu, đơn vị, đường thời gian và thời gian bắt đầu log. Nếu cần nén, dùng tùy chọn DEFLATE (`1`); mặc định không nén phù hợp cho kiểm tra tương thích đầu tiên.

Bộ tool có sẵn `examples/demo.blf`, `demo.dbc` và kết quả `demo.mf4`, `demo_001.xlsx` để thử nhanh. Giá trị đầu của `CAN1.Powertrain.Speed` là **60 km/h**, `CAN1.Powertrain.Torque` là **−50 Nm**, `CAN1.BatteryFD.PackVoltage` là **350 V**. Các frame ID `0x700` trong dữ liệu mẫu cố ý không có trong DBC để minh họa báo cáo ID chưa biết.

Chỉ tín hiệu **khớp DBC** được xuất. Frame không có DBC, error frame, remote frame và các loại object khác trong BLF không trở thành kênh tín hiệu; báo cáo có thống kê. Frame ngắn hơn độ dài message trong DBC bị bỏ qua và đếm riêng. **Giữ BLF gốc** để lưu raw bus, các dữ liệu ngoài CAN và các ID chưa biết. Chưa có cơ chế J1939 PGN động, AUTOSAR container/PDU, Ethernet, LIN hay FlexRay. DBC dùng ID CAN chính xác và phân biệt standard/extended. Log có thời gian đi lùi trong cùng nhóm tín hiệu sẽ báo lỗi thay vì tạo MF4 có timebase không hợp lệ.

Tool diễn giải SYSTEMTIME trong header BLF là **UTC**, thống nhất với `python-can 4.6.1`. Thời gian lấy mẫu tương đối được giữ nguyên; vì header không có trường múi giờ riêng, cần đối chiếu cách logger ghi thời gian nếu thời gian tuyệt đối hiển thị khác mong đợi.

## Chạy bằng dòng lệnh

Trong thư mục đã cài bằng `Run_Windows.bat`:

```bat
.venv\Scripts\python.exe -m blf_converter log.blf --dbc CAN1=vehicle.dbc --output D:\Converted --format both
```

```bat
.venv\Scripts\python.exe -m blf_converter log1.blf log2.blf --dbc CAN1=powertrain.dbc --dbc CAN2=body.dbc --output D:\Converted --format mf4 --chunk-mb 64
```

`--format` nhận `mf4`, `excel` hoặc `both`. `--compress` bật DEFLATE cho MF4. `Ctrl+C` yêu cầu hủy và dọn đầu ra dở dang.

## Đóng gói thành EXE

Sau lần cài đầu, đóng ứng dụng rồi chạy **Build_Windows.bat** trên Windows. Kết quả ở `dist\BLFConverter\BLFConverter.exe`. Copy **toàn bộ thư mục** `BLFConverter` sang máy Windows đích; máy đích không cần cài Python. Chọn dạng thư mục để tránh giải nén một EXE lớn mỗi lần khởi động.

Script build được cung cấp để chạy trên Windows; bản EXE Windows chưa được build/kiểm thử trong môi trường Linux hiện tại. Các thư viện được đóng gói có giấy phép riêng, cần giữ license/notice do PyInstaller thu thập khi phân phối.

## Dữ liệu mẫu, kiểm thử và đo hiệu năng

Cho nhà phát triển, trong Python 3.12 64 bit:

```bat
python -m pip install -e ".[dev]"
python -m pytest -q
python tools/generate_demo.py demo --frames 10000
python tools/benchmark.py benchmark-output --frames 100000 --excel-frames 10000
```

`examples/demo.dbc` và bộ sinh BLF bao gồm CAN FD 64 byte, signed/scaled, Intel/Motorola, multiplex, standard/extended và bộ đếm 64 bit. Benchmark ghi JSON với tốc độ đọc và thời gian chuyển đổi; đó là dữ liệu tổng hợp, không dùng để cam kết thời gian cho log xe thực tế 3 GB.
