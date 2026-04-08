import tkinter as tk
from tkinter import messagebox


def main() -> None:
    root = tk.Tk()
    root.title("Hello")
    root.geometry("300x120")

    root.after(100, lambda: messagebox.showinfo("提示", "你好，世界"))

    label = tk.Label(root, text="程序已启动")
    label.pack(expand=True)

    root.mainloop()


if __name__ == "__main__":
    main()
