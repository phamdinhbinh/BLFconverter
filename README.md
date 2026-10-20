# BLF Converter

Tool chạy cục bộ trên **Windows 10/11 64 bit**, chuyển **BLF + DBC → MF4 và Excel XLSX**, hỗ trợ **CAN/CAN FD**. Giao diện tiếng Việt, xử lý theo luồng để ưu tiên tốc độ và tránh nạp toàn bộ log vào RAM.

## Chạy ứng dụng

1. Tải mã nguồn và giải nén, hoặc clone repository.
2. Dùng **Python 3.12, 3.13 hoặc 3.14 64 bit** có sẵn, có thành phần **Tcl/Tk**. Không cần cài thêm Python 3.12 nếu máy đã có 3.14.
3. Mở `Run_Windows.bat`. Script tự chọn Python và tạo môi trường `.venv` trong thư mục tool; lần đầu cần mạng để tải thư viện dạng wheel. Không cần quyền Administrator hoặc kích hoạt PowerShell script. Chuyển đổi log sau đó chạy cục bộ.
4. Chọn BLF, thêm DBC và gán đúng kênh CAN, chọn thư mục kết quả rồi bắt đầu chuyển đổi.

Để chuyển nhanh nhất, chọn riêng **MF4**, giữ mặc định **không nén** và dùng SSD. Excel tự chia sheet/workbook khi cần; xuất Excel có thể chiếm phần lớn thời gian xử lý. Có thể chạy `Build_Windows.bat` để đóng gói EXE trên Windows.

Nếu dùng VS Code với Python 3.14, mở thư mục dự án, chọn **Terminal → New Terminal** rồi chạy:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe -m blf_converter
```

Nếu máy không có lệnh `py`, dùng `python -m venv .venv` ở dòng đầu, sau khi kiểm tra `python --version` là 3.14. Nếu đã có `.venv` từ lần cài lỗi trước, đóng ứng dụng/terminal đang dùng môi trường đó rồi đổi tên thư mục thành `.venv_old` trước khi tạo môi trường mới. Xem thêm [hướng dẫn máy công ty và cài offline](README_VI.md#python-314-vs-code-và-máy-công-ty).

## Xử lý và kiểm thử

- Đọc BLF trực tiếp theo từng khối; giải mã DBC bằng NumPy.
- Ghi **MDF 4.10**, giữ timestamp gốc và đơn vị tín hiệu; không resample.
- Ghi XLSX theo luồng; có báo cáo ID chưa giải mã và thời gian từng giai đoạn.
- **55 kiểm thử đạt**; có dữ liệu mẫu và kết quả trong `examples/`.

Benchmark tổng hợp trên Linux: **1 triệu frame → MF4 khoảng 2,7 giây**. Đây không phải kết quả trên Windows hoặc log xe thực tế 3 GB, không dùng để cam kết tốc độ trên máy đích. **ETAS MDA 8.6 là ứng dụng đích; chưa xác nhận trực tiếp khả năng mở file bằng MDA 8.6 trong môi trường phát triển.** Có thể dùng `examples/demo.mf4` để kiểm tra trước.

Xem [hướng dẫn đầy đủ](README_VI.md), [kết quả đo hiệu năng](BENCHMARK.md) và [kết quả kiểm thử](verification/test-results.txt). Giữ BLF gốc để lưu các frame không khớp DBC và dữ liệu ngoài phạm vi tín hiệu được xuất.
