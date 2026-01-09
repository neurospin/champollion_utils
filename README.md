# Champollion Utils

**Shared utilities for Champollion projects.**

[![License: CeCILL](http://www.cecill.info/licences/Licence_CeCILL_V2.1-fr.txt)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

---

## 📦 Overview
`champollion_utils` provides **reusable Python utilities** for the **Champollion Pipeline**, **Champollion v1**, and **DeepFolding** projects. It includes:
- **`ScriptBuilder`**: A flexible command-line script builder (Builder pattern).

---

## 🛠 Installation

### Via Pixi (Recommended)
Add to your `pixi.toml`:
```toml
[dependencies]
champollion-utils = { url = "https://github.com/neurospin/champollion_utils.git", editable = true }