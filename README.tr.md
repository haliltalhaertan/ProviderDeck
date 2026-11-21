# ProviderDeck

[English README](README.md)

**Abonelik kotalarını** (5 saatlik + haftalık), **API cüzdan bakiyelerini** ve **iş yüküne göre en ucuz model rotalarını** tek ekranda izleyen; **yerel, salt-okunur ve telemetrisiz** web paneli ve Windows sistem tepsisi aracı.

Sıfır frontend build adımı (saf HTML/CSS/JS, CDN yok) ve hafif Python yerel HTTP servisi ile çalışır.

---

## Özellikler

- **Kodlama Abonelikleri (Haftalık + 5 Saatlik Kotalar):**
  - Anthropic / Claude Code (`~/.claude/.credentials.json` veya OAuth)
  - OpenAI ChatGPT / Codex (`~/.codex/auth.json`)
  - Google Antigravity (`agy -p /usage --output-format json` + `/credits`, model isteği harcamaz)
  - Meta Muse Code CLI (`muse usage --json`, Linux/macOS'ta doğrudan, Windows'ta WSL üzerinden)
  - GitHub Copilot ve OpenCode Go / Zen
- **15+ API Cüzdanı ve Katalog:**
  - OpenRouter, Vercel AI Gateway, Experiential Labs, UberLLM, Venice, VoidAI, Refuseless, OrcaRouter, Outrun, NanoGPT, NVIDIA NIM, Meta Model API, Nous Portal ve `config.yaml` ile özel OpenAI-uyumlu geçitler.
- **İş Yüküne Göre Maliyet Tahmini & Aynı-Model Karşılaştırması:**
  - Girdi token, çıktı token ve önbellek isabet oranına (`cache%`) göre gerçek tahmini maliyeti hesaplar; yalnızca birebir aynı `canonical_key` altındaki rotaları yan yana karşılaştırır.
- **Native Windows Sistem Tepsisi:**
  - Üzerine gelindiğinde pozitif USD toplamını ve seçili tüm aboneliklerin haftalık + `5s` kalan yüzdelerini gösterir; sağ tık menüsüyle abonelikler gizlenip açılabilir.
- **Salt-Okunur Bakım Sayfası (`/maintenance`):**
  - Kalıcı şema/uç nokta entegrasyon hatalarını izler, yalnızca izinli resmi dokümanları kontrol eder; hiçbir kodu veya ayarı otomatik değiştirmez.

---

## Kurulum ve Çalıştırma

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux / macOS:
# source .venv/bin/activate

pip install -r requirements.txt
```

1. API anahtarlarınız için `.env.example` dosyasını `.env` olarak kopyalayıp kullandığınız anahtarları doldurun.
2. Başlatın:
   ```bash
   python launch.py
   ```
   *(Windows'ta doğrudan `scripts/start.cmd` ve `scripts/stop.cmd` kullanılabilir.)*

---

## Testler

```bash
python -m unittest discover -s tests -p "test_*.py" -v
node tests/ui.test.cjs
```

## Lisans

[GNU GPL v3](LICENSE)
