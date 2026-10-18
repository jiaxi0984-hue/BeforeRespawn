"""触发后才显示的下载确认：非阻塞、可取消、不执行任何网络或下载。"""
import queue
import threading
import tkinter as tk
import unittest
from app import App


class DownloadPromptTests(unittest.TestCase):
    def app(self,root=None):
        app=App.__new__(App);app.root=root;app.closing=False
        app.stop_event=threading.Event();app.events=queue.Queue()
        return app

    def test_revive_or_pause_closes_prompt_and_denies_download(self):
        for pause in [False,True]:
            with self.subTest(pause=pause):
                root=tk.Tk();root.withdraw();app=self.app(root)
                reply=threading.Event();cancelled=threading.Event();answer={'approved':False}
                try:
                    dialog=app.show_download_prompt((reply,answer,cancelled))
                    root.after(20,app.stop_event.set if pause else cancelled.set)
                    root.after(180,root.quit);root.mainloop()
                    self.assertTrue(reply.is_set());self.assertFalse(answer['approved'])
                    self.assertFalse(dialog.winfo_exists())
                finally:root.destroy()

    def test_download_worker_only_enqueues_ui_request(self):
        app=self.app();cancelled=threading.Event();result=[]
        worker=threading.Thread(target=lambda:result.append(app.confirm_download(cancelled)))
        worker.start()
        kind,(reply,answer,event)=app.events.get(timeout=1)
        self.assertEqual(kind,'download_request');self.assertIs(event,cancelled)
        answer['approved']=True;reply.set();worker.join(1)
        self.assertEqual(result,[True])

    def test_cancelled_request_does_not_create_a_dialog(self):
        app=self.app();cancelled=threading.Event();cancelled.set();reply=threading.Event()
        self.assertIsNone(app.show_download_prompt((reply,{'approved':False},cancelled)))
        self.assertTrue(reply.is_set())
