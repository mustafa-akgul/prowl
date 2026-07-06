---
name: add-mode
description: Scaffold a new review mode for the review agent. Guides through creating the prompt template, registering the mode in config_manager.py, updating the CLI choices, the GitHub Actions workflow regex, and reminds to write a test. Use when the user wants to add a new review mode like "accessibility", "performance", "dependency-audit", etc.
argument-hint: <mode-name> [short description]
allowed-tools: Read, Edit, Write, Glob, Grep
---

# Yeni Review Modu Ekleme — $ARGUMENTS

`$ARGUMENTS` argümanından mode adını ve açıklamayı parse et (ilk kelime = mode adı, kalan = kısa açıklama).

Aşağıdaki adımları **sırayla** uygula. Her adımda yaptığın değişikliği kullanıcıya bildir.

---

## Adım 1 — Prompt Template Oluştur

`agent/prompts/<MODE_NAME>_review.md` dosyasını oluştur. Mevcut templatelerden birini (`base_review.md`, `security_review.md`) referans alarak aynı yapıyı koru. Şu placeholder'ları mutlaka içermelidir:

- `{jira_context}` — Jira ticket bağlamı
- `{pm_instructions}` — PM'den gelen ek talimatlar
- `{diff}` — PR diff içeriği

Template içeriği, mode'un amacına özel yönergeler içermeli (örn. security modu için OWASP top 10, accessibility modu için WCAG kriterleri).

## Adım 2 — Mode'u config_manager.py'ye Kaydet

`agent/config_manager.py` dosyasını oku. `load_prompt()` ve `save_prompt()` fonksiyonlarının içindeki `mode_to_file` sözlüklerine yeni satırı ekle:

```python
"<MODE_NAME>": "<MODE_NAME>_review.md",
```

Her iki sözlüğe de eklemeyi unutma.

## Adım 3 — CLI choices Güncelle

`agent/review_agent.py` dosyasını oku. `_parse_args()` fonksiyonundaki `--mode` argümanının `choices` listesine yeni mode adını ekle.

Ayrıca `run()` fonksiyonunun `mode` parametresinin `Literal[...]` type annotation'ına da ekle.

## Adım 4 — GitHub Actions Workflow Güncelle

`.github/workflows/pm-command.yml` dosyasını oku. Mode regex'ini bulup yeni mode adını pattern'a ekle.

## Adım 5 — Eksik Test Hatırlatması

`tests/` dizinini kontrol et. Yeni mode için test dosyası oluşturulmadıysa kullanıcıya şunu söyle:

> `tests/` altına yeni mode için en az bir test eklemeyi unutmayın. Mevcut `test_review_agent.py`'ye benzer bir test senaryosu yeterlidir.

---

Tüm adımlar tamamlandıktan sonra özet bir değişiklik listesi sun.
