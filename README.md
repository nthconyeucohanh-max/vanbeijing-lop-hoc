# Tiếng Trung Vanbeijing — lớp 8 học viên

- Học viên nhập Tên/Nick, không email và không mật khẩu.
- Nick mới tự tạo hồ sơ, tối đa 8 học viên; nick cũ mở lại đúng hồ sơ.
- Trình duyệt nhớ nick. Đổi máy cần nhập lại đúng nick.
- Giáo viên: tên đăng nhập `Van`, mật khẩu khởi tạo `Van` (đặt VANBEIJING_TEACHER_PASSWORD=Van khi triển khai).
- /classroom: điểm riêng 10 bài, tiến độ, thời gian từng bài và tổng thời gian; giáo viên xem lịch sử bài làm.
- /learn: 10 bài Msutong quyển 3; giữ nguyên nội dung đã sửa và 20 file nghe. Nghe chép cả bài; đáp án xuống dòng, mỗi lượt thoại một gạch đầu dòng.
- Câu đúng tính 1 điểm, mỗi bài tối đa 22; làm lại không cộng trùng; xem đáp án trước không cộng điểm câu đó.
- Bản nháp tự lưu sau khi ngừng gõ 0,7 giây; cần có mạng. Hãy đợi lưu trước khi đóng trang.
- Thời gian chỉ tính khi tab hiện và có thao tác trong 2 phút gần nhất hoặc đang phát âm thanh; không cộng trùng giữa các tab.
- Vì không dùng mật khẩu học viên, người nhập đúng nick có thể vào hồ sơ đó. Dùng nick khác nhau cho mỗi bạn.

## Chạy
Python 3.12. Cài `pip install -r requirements.txt`, sau đó `python app.py`.
Mở http://localhost:8000.

## Railway
Dùng Dockerfile kèm theo. PORT do Railway cấp.
Gắn volume vào /data; đặt VANBEIJING_DB=/data/vanbeijing.db.
VANBEIJING_PRODUCTION=true, VANBEIJING_SECRET_KEY là chuỗi ngẫu nhiên ít nhất 32 ký tự,
VANBEIJING_TEACHER_PASSWORD=Van, VANBEIJING_TIMEZONE=Asia/Ho_Chi_Minh.
Không đưa cơ sở dữ liệu và khóa bí mật vào kho mã. Kiểm tra /health sau khi chạy.

Bản này đã được kiểm tra cục bộ; chưa triển khai thành công lên internet trong lượt chỉnh sửa này.
