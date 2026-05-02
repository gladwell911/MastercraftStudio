# Sibling History Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make packaged builds store app data in the executable's sibling `history` directory and produce `c:\code\cx\mc\mc.exe`.

**Architecture:** Keep source-run behavior unchanged, but when `sys.frozen` is true resolve the data directory from `sys.executable` so the app writes beside the packaged program rather than into a temporary extraction tree. Update PyInstaller naming so the produced executable is `mc.exe`, then verify with focused tests and a fresh build.

**Tech Stack:** Python 3.11, wxPython, pytest, PyInstaller

---
