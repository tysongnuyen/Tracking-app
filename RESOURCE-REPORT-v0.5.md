# Báo cáo tài nguyên · Tracking v0.5

Đo trực tiếp bản EXE Windows trên máy hiện tại ngày 17/10/2026. Mỗi trạng thái được chạy riêng khoảng 45 giây, tự thoát; dùng database kiểm tra riêng trong work/. Không đưa hoạt động kiểm tra vào lịch sử thật.

| Trạng thái | Thời lượng | CPU trung bình toàn máy | RAM RSS trung bình | RAM RSS đỉnh | Số tiến trình đỉnh | UIA worker được tạo |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Chỉ chạy nền | 45.8 giây | 0.384% | 79.2 MiB | 80.3 MiB | 2 | Có |
| Mở GUI + chạy bộ thu thập | 45.7 giây | 0.547% | 116.7 MiB | 118.2 MiB | 3 | Có |

Máy có 16 CPU logic. CPU trên một lõi lần lượt là 6.15% và 8.75%; cột toàn máy chia các giá trị này cho 16.

RAM là tổng working set/RSS của bộ thu thập và các tiến trình con (GUI/UIA nếu có); có thể đếm trùng trang nhớ chia sẻ. Tiến trình bootloader chờ của gói onefile không nằm trong cây này. CPU gồm khởi động và chi phí đo. Không bao gồm CPU phát sinh bên trong trình duyệt do UI Automation hay extension. Không đo điện năng hoặc bộ nhớ của extension.

Đây là phép đo ngắn dưới bố cục cửa sổ/workload đang có, không phải bảo đảm mức tài nguyên khi mở nhiều tab hoặc dùng app cả ngày. GUI được kiểm tra chức năng bằng dữ liệu minh họa và self-check; phép đo GUI này dùng dữ liệu thật của phiên kiểm tra ngắn nên danh sách còn ít. Đóng GUI sẽ kết thúc tiến trình giao diện, giữ bộ thu thập và khay hệ thống.

Dung lượng EXE: 20.9 MiB. Dung lượng database sau mỗi phiên kiểm tra:

- background: 36 KiB, 6 dòng tổng hợp ứng dụng/website, 2 dòng website theo browser.
- dashboard: 36 KiB, 6 dòng tổng hợp ứng dụng/website, 2 dòng website theo browser.

Không suy ra tốc độ tăng database theo ngày từ hai phiên ngắn. App giữ tổng hợp ngày 30 ngày, có giới hạn nguồn/ngày; không ghi mẫu thô mỗi giây.

45 unit test Python, kiểm tra extension và self-check của bản EXE đều đã qua. Tách website theo browser được kiểm tra bằng fixture UIA/bridge; chưa xác nhận tương thích Split View trên mọi browser thật. Báo cáo JSON gốc ở verification/runtime-background.json và verification/runtime-dashboard.json.
