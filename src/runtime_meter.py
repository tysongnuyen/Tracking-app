"""Measure the owned tracker process tree; no application or website names logged."""
import threading
import time
import psutil

class RuntimeMeter:
    def __init__(self):
        self.stop_event=threading.Event()
        self.cpu={};self.peak=0;self.peak_processes=0;self.rss_samples=[]
        self.started=time.perf_counter()
        self.thread=threading.Thread(target=self._run,daemon=True,name="RuntimeMeter")
    def start(self):self.thread.start()
    def _run(self):
        parent=psutil.Process()
        while not self.stop_event.is_set():
            processes=[parent]+parent.children(recursive=True)
            rss=0;count=0
            for process in processes:
                try:
                    cpu=process.cpu_times();rss+=process.memory_info().rss;count+=1
                    self.cpu[(process.pid,process.create_time())]=cpu.user+cpu.system
                except psutil.Error:pass
            self.peak=max(self.peak,rss);self.peak_processes=max(self.peak_processes,count)
            self.rss_samples.append(rss)
            self.stop_event.wait(.5)
    def finish(self):
        self.stop_event.set();self.thread.join(1)
        elapsed=time.perf_counter()-self.started
        logical=psutil.cpu_count() or 1
        percent=sum(self.cpu.values())/elapsed*100
        return dict(status="PASS",elapsed_seconds=round(elapsed,2),peak_process_tree_rss_mib=round(self.peak/1048576,1),
            mean_process_tree_rss_mib=round(sum(self.rss_samples)/max(1,len(self.rss_samples))/1048576,1),
            cpu_one_core_percent=round(percent,2),cpu_machine_percent=round(percent/logical,3),logical_cpus=logical,
            peak_tracker_processes=self.peak_processes,dashboard_opened=False,
            note="Short live tray/Windows collector run. CPU includes startup; sums sampled owned-process CPU times. RSS sum can double-count shared pages. Browser activity and window layout affect results; no resource guarantee.")
