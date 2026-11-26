"""Nhận thông báo khi người dùng đổi, di chuyển hoặc thu nhỏ cửa sổ.

Windows sẽ gọi hàm changed() khi có sự kiện đã đăng ký. Cách đăng ký này gọi
là hook. File này tăng các bộ đếm để những phần khác của Tracking biết cần
đọc lại thông tin cửa sổ hoặc website.
"""
import ctypes as c
from ctypes import wintypes as w
import threading

class WindowEvents:
    """Đăng ký nhận thông báo từ Windows và đếm các lần cửa sổ thay đổi.

    Ví dụ, bạn kéo Chrome sang bên phải thì revision tăng. WindowsSampler
    thấy số này thay đổi sẽ kiểm tra lại vị trí các cửa sổ.
    Khi tên cửa sổ browser thay đổi, bộ đếm trong names tăng để BrowserProbe
    biết cần đọc lại thanh địa chỉ.
    """
    def __init__(self):
        """Chuẩn bị các bộ đếm và thông tin dùng để bắt đầu hoặc dừng theo dõi."""
        # Số này tăng khi có sự kiện cần kiểm tra lại vị trí/độ hiển thị cửa sổ.
        self.revision=0
        # Lưu số lần đổi tên của từng cửa sổ. HWND là mã Windows cấp cho cửa sổ.
        self.names={}
        # Luồng nền thực hiện việc chờ thông báo từ Windows.
        self.thread=None
        # Mã của luồng nền, dùng để gửi yêu cầu dừng cho đúng luồng.
        self.thread_id=0
        # Tín hiệu cho start() biết bước chuẩn bị nhận thông báo đã hoàn tất.
        self.ready=threading.Event()
    def start(self):
        """Bắt đầu luồng chờ thông báo và đợi nó chuẩn bị tối đa 1 giây."""
        self.thread=threading.Thread(target=self._run,daemon=True,name="WindowEvents")
        self.thread.start()
        self.ready.wait(1)
    def title_revision(self,hwnd):
        """Trả số lần đã ghi nhận đổi tên của cửa sổ có mã hwnd.

        Chưa có lần nào thì trả 0. BrowserProbe so số này với lần trước để
        quyết định có cần đọc lại thanh địa chỉ hay không.
        """
        return self.names.get(hwnd,0)
    def _run(self):
        """Đăng ký những thay đổi cần theo dõi, rồi chờ Windows thông báo.

        Windows gọi changed() khi có sự kiện phù hợp. Hàm này chạy ở luồng
        nền và chờ cho đến khi close() gửi yêu cầu thoát. Khi thoát, hủy các
        đăng ký đã tạo để giải phóng tài nguyên.
        """
        # Nạp các hàm Windows dùng để nhận thông báo và lấy mã luồng hiện tại.
        user=c.WinDLL("user32",use_last_error=True)
        kernel=c.WinDLL("kernel32",use_last_error=True)
        kernel.GetCurrentThreadId.restype=w.DWORD
        self.thread_id=kernel.GetCurrentThreadId()
        # Khai báo dạng hàm mà Windows có thể gọi lại trong Python.
        callback_type=c.WINFUNCTYPE(None,w.HANDLE,w.DWORD,w.HWND,w.LONG,w.LONG,w.DWORD,w.DWORD)
        @callback_type
        def changed(hook,event,hwnd,obj,child,thread,stamp):
            """Nhận một thông báo từ Windows rồi cập nhật bộ đếm tương ứng.

            event là loại thay đổi; hwnd là mã cửa sổ xảy ra thay đổi.
            Hàm do Windows gọi như thế này thường được gọi là callback.
            """
            # Bỏ các thay đổi của thành phần con, chẳng hạn một nút trong cửa sổ.
            if event>=0x8000 and (obj!=0 or child!=0):
                return
            # 0x800C là thông báo tên cửa sổ đã thay đổi.
            if event==0x800C and hwnd:
                # Xóa bộ đếm cũ nếu danh sách quá lớn để hạn chế bộ nhớ sử dụng.
                if len(self.names)>1024:self.names.clear()
                self.names[int(hwnd)]=self.names.get(int(hwnd),0)+1
            # Các sự kiện còn lại báo rằng cần kiểm tra lại bố cục cửa sổ.
            else:self.revision+=1
        # Khai báo kiểu tham số và kết quả để Python gọi các hàm Windows đúng cách.
        user.SetWinEventHook.argtypes=[w.DWORD,w.DWORD,w.HMODULE,callback_type,w.DWORD,w.DWORD,w.DWORD]
        user.SetWinEventHook.restype=w.HANDLE
        user.UnhookWinEvent.argtypes=[w.HANDLE]
        # Đăng ký hàm changed() cho các nhóm sự kiện:
        # 3: đổi cửa sổ có focus; 0x16-0x17: bắt đầu/kết thúc thu nhỏ cửa sổ.
        # 0x8000-0x800C: tạo, đóng, hiện, ẩn, đổi vị trí hoặc đổi tên đối tượng.
        # Giá trị 2 cuối lời gọi yêu cầu bỏ qua sự kiện từ chính process Tracking.
        hooks=[user.SetWinEventHook(a,b,None,changed,0,0,2) for a,b in ((3,3),(0x16,0x17),(0x8000,0x800C))]
        message=w.MSG()
        self.ready.set()
        try:
            # Chờ và xử lý thông báo để Windows có thể gọi changed().
            # GetMessageW chờ khi chưa có thông báo, giúp luồng nghỉ lúc rảnh.
            while user.GetMessageW(c.byref(message),None,0,0)>0:
                user.TranslateMessage(c.byref(message));user.DispatchMessageW(c.byref(message))
        finally:
            # Hủy từng đăng ký khi kết thúc theo dõi.
            for hook in hooks:
                if hook:user.UnhookWinEvent(hook)
    def close(self):
        """Gửi thông báo thoát cho luồng nền, rồi chờ nó kết thúc tối đa 1 giây.

        0x12 là mã WM_QUIT của Windows, làm vòng GetMessageW trong _run() dừng.
        """
        if self.thread_id:
            c.WinDLL("user32").PostThreadMessageW(self.thread_id,0x12,0,0)
        if self.thread:self.thread.join(1)
