#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Client_main.py ― 受付システム Tkinter クライアント
---------------------------------------------------
"""

import argparse
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional

import requests

API_BASE = "http://localhost:8000"

class ApiClient:
    def __init__(self, base: str = API_BASE):
        self.base = base.rstrip("/")

    def get_services(self):
        return requests.get(self.base + "/services", timeout=5).json()

    def register_ticket(self, name: str, service_id: int):
        r = requests.post(self.base + "/tickets",
                          json={"name": name, "service_id": service_id}, timeout=5)
        r.raise_for_status()
        return r.json()

    def queue_detail(self, service_id: int):
        r = requests.get(self.base + f"/queues/{service_id}", timeout=5)
        r.raise_for_status()
        return r.json()

class ClientGUI:
    def __init__(self, root: tk.Tk, api: ApiClient):
        self.root = root
        self.api = api
        self.root.title("受付 (Client)")
        self.ticket_ids: List[int] = []
        self.current_service_id: Optional[int] = None

        # 上部フレーム：全体情報
        frm_top = ttk.Frame(root, padding=10)
        frm_top.pack(fill="x")
        ttk.Label(frm_top, text="サービス:").pack(side="left")

        self.cmb_var = tk.StringVar()
        self.cmb = ttk.Combobox(frm_top, state="readonly", textvariable=self.cmb_var, width=25)
        self.cmb.pack(side="left", padx=5)
        ttk.Button(frm_top, text="更新", command=self.refresh_services).pack(side="left")
        self.cmb.bind("<<ComboboxSelected>>", lambda e: self.update_queue_info())

        self.waiting_var = tk.StringVar(value="-")
        ttk.Label(frm_top, text="　待ち人数:").pack(side="left")
        ttk.Label(frm_top, textvariable=self.waiting_var, font=("Helvetica", 12, "bold")).pack(side="left", padx=3)

        # 呼び出された番号/名前リスト
        frm_called = ttk.Frame(root, padding=(10, 2))
        frm_called.pack(fill="x")
        ttk.Label(frm_called, text="呼び出された人:").pack(side="left")
        self.called_var = tk.StringVar(value="")
        self.called_label = ttk.Label(frm_called, textvariable=self.called_var, font=("Helvetica", 12, "bold"), foreground="red")
        self.called_label.pack(side="left")

        # 名前入力＋受付
        frm_name = ttk.Frame(root, padding=10)
        frm_name.pack(fill="x")
        ttk.Label(frm_name, text="名前:").pack(side="left")
        self.name_var = tk.StringVar()
        self.entry_name = ttk.Entry(frm_name, textvariable=self.name_var, width=25)
        self.entry_name.pack(side="left", padx=5)

        self.btn_reg = ttk.Button(root, text="受付する", command=self.register)
        self.btn_reg.pack(pady=5)

        # 受付結果（最新）
        self.msg_label = ttk.Label(root, text="", foreground="green")
        self.msg_label.pack(pady=5)

        # 呼び出し中大画面リスト
        frm_called_box = ttk.LabelFrame(root, text="現在 呼び出し中", padding=10)
        frm_called_box.pack(fill="both", expand=True, padx=10, pady=8)
        self.lst_called = tk.Listbox(frm_called_box, height=6, font=("Helvetica", 18))
        self.lst_called.pack(fill="both", expand=True)

        self.refresh_services()
        self._queue_info_job = None

    def refresh_services(self):
        try:
            svcs = self.api.get_services()
            self.svc_dict = {s["name"]: s["id"] for s in svcs}
            self.cmb["values"] = list(self.svc_dict.keys())
            if svcs:
                self.cmb.current(0)
                self.update_queue_info()
        except Exception as e:
            messagebox.showerror("Error", f"サービス取得失敗:\n{e}")

    def update_queue_info(self):
        # サービス切替時にも毎回呼ばれる
        svc_name = self.cmb_var.get()
        if svc_name not in self.svc_dict:
            return
        self.current_service_id = self.svc_dict[svc_name]
        self.poll_queue_info()

    def poll_queue_info(self):
        if not self.current_service_id:
            return
        try:
            q = self.api.queue_detail(self.current_service_id)
            self.waiting_var.set(str(q["waiting"]))

            # 呼び出された人一覧
            called_list = [f"{t['id']}:{t['name']}" for t in q["tickets"] if t["called"]]
            self.called_var.set(" / ".join(called_list[-3:]))  # 最新3名だけテキストで
            self.lst_called.delete(0, "end")
            for t in q["tickets"]:
                if t["called"]:
                    self.lst_called.insert("end", f"{t['id']}: {t['name']}")
        except Exception as e:
            self.waiting_var.set("-")
            self.called_var.set("")
            self.lst_called.delete(0, "end")
        # 3秒ごとに更新
        self._queue_info_job = self.root.after(3000, self.poll_queue_info)

    def register(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("入力不足", "名前を入力してください")
            return
        svc_name = self.cmb_var.get()
        if svc_name not in self.svc_dict:
            messagebox.showwarning("入力不足", "サービスを選択してください")
            return
        try:
            res = self.api.register_ticket(name, self.svc_dict[svc_name])
            self.ticket_ids.append(res["id"])
            self.msg_label["text"] = f"チケット発行: #{res['id']}（{name}）"
            self.msg_label["foreground"] = "green"
            self.name_var.set("")  # 受付後にフォームクリア
            self.entry_name.focus_set()
        except Exception as e:
            messagebox.showerror("Error", f"受付失敗:\n{e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("client", "admin"), default="client")
    parser.add_argument("--host", default=API_BASE)
    args = parser.parse_args()

    if args.mode != "client":
        print("このプログラムは --mode client でのみ動作します。adminモードは別実装です。")
        return

    api = ApiClient(args.host)
    root = tk.Tk()
    root.geometry("700x500")
    ClientGUI(root, api)
    root.mainloop()

if __name__ == "__main__":
    main()
