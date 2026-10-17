# Bağış Takip

Küçük dernekler için bağış takip uygulaması.

## Teknoloji
- Laravel 11, PHP 8.2, MySQL; testler Pest ile.
- Arayüz Blade + Tailwind; tasarımlar TASARIM/ klasöründe.

## Mimari
- Controller ince kalır; iş mantığı app/Services altında.
- Veritabanına yalnızca Eloquent modelleri üzerinden erişilir.

## Aşamalar
1. Giriş ekranı (e-posta + şifre).
2. Panel: bağış listesi ve toplam.
