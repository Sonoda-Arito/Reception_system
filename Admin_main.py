#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
queue_gui.py  ―  受付システム Tkinter クライアント
---------------------------------------------------
▶ 起動例
    # 来場者
    python queue_gui.py --mode client

    # スタッフ
    python queue_gui.py --mode admin

※ FastAPI サーバー (queue_server.py) を別プロセスで動かしておくこと
"""

from __future__ import annotations

import argparse
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional

import requests

API_BASE = "http://localhost:8000"
ADMIN_API_KEY = "CHANGE_ME"  # queue_server.py と合わせる


# ───────────────────────────────────────────────
# REST API ラッパ
# ───────────────────────────────────────────────
class ApiClient:
    def __init__(self, base: str = API_BASE):
        self.base = base.rstrip("/")

    # ---------- Service ----------
    def get_services(self) -> List[Dict[str, Any]]:
        return self._get("/services")

    def add_service(self, name: str, description: str | None = None):
        return self._post("/services", json={"name": name, "description": description})

    # ---------- Ticket ----------
    def register_ticket(self, name: str, service_id: int):
        return self._post("/tickets", json={"name": name, "service_id": service_id})

    def get_ticket(self, ticket_id: int):
        return self._get(f"/tickets/{ticket_id}")

    def cancel_ticket(self, ticket_id: int):
        self._delete(f"/tickets/{ticket_id}")

    # ---------- Queue ----------
    def queue_detail(self, service_id: int):
        return self._get(f"/queues/{service_id}")

    def stats(self):
        return self._get("/stats")

    # ---------- Admin ----------
    def call_next(self, service_id: int):
        return self._post(f"/admin/next/{service_id}",
                          headers={"x-api-key": ADMIN_API_KEY})

    # ---------- 内部 ----------
    def _get(self, path, **kw):
        r = requests.get(self.base + path, timeout=5, **kw)
        r.raise_for_status()
        return r.json()

    def _post(self, path, **kw):
        r = requests.post(self.base + path, timeout=5, **kw)
        r.raise_for_status()
        return r.json()

    def _delete(self, path, **kw):
        r = requests.delete(self.base + path, timeout=5, **kw)
        r.raise_for_status()


# ───────────────────────────────────────────────
# Tkinter 共通ユーティリティ
# ───────────────────────────────────────────────
def async_api(func):
    """API 呼び出しを別スレッドで実行し、完了時に mainloop へ戻すデコレータ"""
    def wrapper(self, *args, **kwargs):
        def task():
            try:
                result = func(self, *args, **kwargs)
                self.root.after(0, lambda: self._on_success(result))
            except Exception as e:
                self.root.after(0, lambda: self._on_error(e))
        threading.Thread(target=task, daemon=True).start()
    return wrapper


# ───────────────────────────────────────────────
# 来場者 GUI
# ───────────────────────────────────────────────
class ClientGUI:
    def __init__(self, root: tk.Tk, api: ApiClient):
        self.root = root
        self.api = api
        self.root.title("受付 (Client)")
        self.ticket_id: Optional[int] = None

        # ----- UI 構築 -----
        frm_top = ttk.Frame(root, padding=10)
        frm_top.pack(fill="x")
        ttk.Label(frm_top, text="サービス:").pack(side="left")

        self.cmb_var = tk.StringVar()
        self.cmb = ttk.Combobox(frm_top, state="readonly",
                                textvariable=self.cmb_var, width=25)
        self.cmb.pack(side="left", padx=5)
        ttk.Button(frm_top, text="更新", command=self.refresh_services).pack(side="left")

        frm_name = ttk.Frame(root, padding=10)
        frm_name.pack(fill="x")
        ttk.Label(frm_name, text="名前:").pack(side="left")
        self.name_var = tk.StringVar()
        ttk.Entry(frm_name, textvariable=self.name_var, width=25).pack(side="left", padx=5)

        self.btn_reg = ttk.Button(root, text="受付する", command=self.register)
        self.btn_reg.pack(pady=5)

        frm_pos = ttk.Frame(root, padding=10)
        frm_pos.pack(fill="x")
        ttk.Label(frm_pos, text="現在位置:").pack(side="left")
        self.pos_var = tk.StringVar(value="-")
        ttk.Label(frm_pos, textvariable=self.pos_var,
                  font=("Helvetica", 18, "bold")).pack(side="left", padx=5)

        self.msg_label = ttk.Label(root, text="", foreground="green")
        self.msg_label.pack(pady=5)

        self.refresh_services()

    # ----- API -----
    def refresh_services(self):
        try:
            svcs = self.api.get_services()
            self.svc_dict = {s["name"]: s["id"] for s in svcs}
            self.cmb["values"] = list(self.svc_dict.keys())
            if svcs:
                self.cmb.current(0)
        except Exception as e:
            messagebox.showerror("Error", f"サービス取得失敗:\n{e}")

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
            self.ticket_id = res["id"]
            self.msg_label["text"] = f"チケット発行: #{self.ticket_id}"
            self.btn_reg["state"] = "disabled"
            self.poll_ticket()
        except Exception as e:
            messagebox.showerror("Error", f"受付失敗:\n{e}")

    def poll_ticket(self):
        if not self.ticket_id:
            return
        try:
            t = self.api.get_ticket(self.ticket_id)
            self.pos_var.set(str(t["position"]))
            if t["position"] == 0:
                self.msg_label.config(text="呼び出しされました！", foreground="red")
                return
        except Exception as e:
            self.msg_label.config(text=f"更新エラー: {e}", foreground="orange")
        self.root.after(3000, self.poll_ticket)


# ───────────────────────────────────────────────
# スタッフ GUI
# ───────────────────────────────────────────────
class AdminGUI:
    def __init__(self, root: tk.Tk, api: ApiClient):
        self.root = root
        self.api = api
        self.root.title("受付 (Admin)")

        # ----- 左ペイン：サービス一覧 -----
        frm_left = ttk.Frame(root, padding=10)
        frm_left.pack(side="left", fill="y")

        ttk.Label(frm_left, text="サービス一覧").pack()
        self.lst = tk.Listbox(frm_left, width=30, height=15)
        self.lst.pack()
        ttk.Button(frm_left, text="更新", command=self.load_stats).pack(pady=2)
        ttk.Button(frm_left, text="次の人を呼ぶ", command=self.call_next).pack(pady=2)

        # ----- サービス追加 -----
        frm_add = ttk.LabelFrame(root, text="サービス追加", padding=10)
        frm_add.pack(side="top", fill="x", padx=10, pady=5)
        ttk.Label(frm_add, text="名前:").grid(row=0, column=0, sticky="e")
        self.add_name = tk.StringVar()
        ttk.Entry(frm_add, textvariable=self.add_name, width=20).grid(row=0, column=1)
        ttk.Label(frm_add, text="説明:").grid(row=1, column=0, sticky="e")
        self.add_desc = tk.StringVar()
        ttk.Entry(frm_add, textvariable=self.add_desc, width=20).grid(row=1, column=1)
        ttk.Button(frm_add, text="追加", command=self.add_service).grid(row=2, column=0, columnspan=2, pady=3)

        # ----- 右ペイン：キュー詳細 -----
        frm_detail = ttk.Frame(root, padding=10)
        frm_detail.pack(side="right", fill="both", expand=True)
        ttk.Label(frm_detail, text="キュー詳細").pack()
        self.txt = tk.Text(frm_detail, width=50, height=15, state="disabled")
        self.txt.pack()

        self.services: List[Dict[str, Any]] = []
        self.load_stats()
        self.lst.bind("<<ListboxSelect>>", lambda _: self.show_detail())
        self.root.after(5000, self.load_stats)  # 5 秒ごとに自動更新

    def load_stats(self):
        try:
            self.services = self.api.stats()
            self.lst.delete(0, "end")
            for s in self.services:
                self.lst.insert("end",
                                f"[{s['service_id']}] {s['service_name']} ({s['waiting']}人待ち)")
        except Exception as e:
            messagebox.showerror("Error", f"統計取得失敗:\n{e}")
        finally:
            self.root.after(5000, self.load_stats)

    def selected_service_id(self) -> Optional[int]:
        sel = self.lst.curselection()
        if not sel:
            return None
        line = self.lst.get(sel[0])
        return int(line.split("]")[0].lstrip("["))

    def show_detail(self):
        svc_id = self.selected_service_id()
        if svc_id is None:
            return
        try:
            q = self.api.queue_detail(svc_id)
            self.txt.config(state="normal")
            self.txt.delete("1.0", "end")
            self.txt.insert("end", f"{q['service_name']}  待ち: {q['waiting']}人\n\n")
            for t in q["tickets"]:
                self.txt.insert("end",
                                f"#{t['id']:04d} {t['name']:<12} Pos:{t['position']} "
                                f"{'呼出済' if t['called'] else ''}\n")
            self.txt.config(state="disabled")
        except Exception as e:
            messagebox.showerror("Error", f"詳細取得失敗:\n{e}")

    def call_next(self):
        svc_id = self.selected_service_id()
        if svc_id is None:
            messagebox.showinfo("選択", "サービスを選択してください")
            return
        try:
            t = self.api.call_next(svc_id)
            messagebox.showinfo("呼び出し",
                                f"Ticket #{t['id']}  {t['name']} を呼び出しました")
            self.show_detail()
            self.load_stats()
        except Exception as e:
            messagebox.showerror("Error", f"呼び出し失敗:\n{e}")

    def add_service(self):
        name = self.add_name.get().strip()
        if not name:
            messagebox.showwarning("入力不足", "サービス名を入力してください")
            return
        desc = self.add_desc.get().strip() or None
        try:
            self.api.add_service(name, desc)
            self.add_name.set("")
            self.add_desc.set("")
            self.load_stats()
        except Exception as e:
            messagebox.showerror("Error", f"追加失敗:\n{e}")


# ───────────────────────────────────────────────
# メイン
# ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("client", "admin"), default="client")
    parser.add_argument("--host", default=API_BASE)
    args = parser.parse_args()

    api = ApiClient(args.host)
    root = tk.Tk()
    if args.mode == "client":
        ClientGUI(root, api)
    else:
        AdminGUI(root, api)
    root.mainloop()


if __name__ == "__main__":
    main()
