---
name: review
description: Run an AI code review for a GitHub PR. Use when the user wants to review a PR, trigger a code review, or test the review agent. Accepts a PR number and optional mode (base/security/performance).
argument-hint: <pr-number> [mode] [instructions]
allowed-tools: Bash(python *), Bash(uvicorn *), Read
---

# AI Code Review — PR İncelemesi

Kullanıcı `$ARGUMENTS` sağladı.

Aşağıdaki adımları izle:

1. **Argümanları parse et**: İlk argüman PR numarası (zorunlu), ikinci argüman mode (`base`, `security`, `performance` — default: `base`), kalan argümanlar ise `--instructions` olarak kullanılır.

2. **`.env` veya `.data/config.json` kontrolü**: `agent/config_manager.py`'yi okuyarak gerekli env var'ların (`GEMINI_API_KEY`, `GITHUB_TOKEN`, `GITHUB_REPO`) set edilip edilmediğini kontrol et. Eksik varsa kullanıcıyı uyar ve dur.

3. **Review komutunu çalıştır**:
   ```bash
   python -m agent.review_agent --pr <PR_NUMBER> --mode <MODE> [--instructions "..."]
   ```

4. **Sonucu raporla**: Komut başarılı olursa review metninin ilk birkaç satırını göster ve PR'a yorum yazıldığını bildir. Hata olursa log çıktısını paylaş ve olası nedeni açıkla (API key eksik, geçersiz PR numarası, rate limit vb.).

## Örnek Kullanımlar

```
/review 42
/review 42 security
/review 42 performance kimlik doğrulama akışlarına özellikle bak
```
