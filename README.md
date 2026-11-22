# Web Crawler & Scraper

Aplikasi web sederhana untuk melakukan crawling dan scraping menggunakan Python Flask.

## Struktur folder

```text
web-crawl-app/
├── app.py
├── requirements.txt
├── .gitignore
├── README.md
└── templates/
    └── index.html
```

## Website target

- `https://quotes.toscrape.com`
- `https://books.toscrape.com`
- `https://itats.ac.id/`

Aplikasi melakukan crawling pada tautan dalam domain yang sama, mengambil data sesuai struktur website, menampilkan hasil dengan pagination, dan menyediakan unduhan CSV.

---

## Cara Menjalankan

### Persiapan

Pastikan **Python 3.10 atau lebih baru** sudah terpasang di Windows.

### Langkah menjalankan

1. Buka folder proyek `web-crawler-app`.
2. Klik kolom alamat folder di File Explorer, ketik `cmd`, lalu tekan **Enter**.
3. Jalankan perintah berikut satu per satu:

   ```bat
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   python app.py
   ```

4. Setelah server berjalan, buka browser dan akses:

   `http://127.0.0.1:5000`

5. Untuk menghentikan aplikasi, kembali ke Command Prompt lalu tekan **Ctrl + C**.

### Menjalankan kembali di lain waktu

Jika virtual environment sudah dibuat dan library sudah terpasang, cukup buka Command Prompt dari folder proyek, lalu jalankan:

```bat
venv\Scripts\activate
python app.py
```

Kemudian buka `http://127.0.0.1:5000` di browser.

---

## Catatan

Aplikasi menggunakan **Requests** dan **BeautifulSoup**, sehingga konten yang hanya dimuat melalui JavaScript atau dilindungi login/CAPTCHA mungkin tidak dapat diambil. Jalankan aplikasi secara lokal untuk keperluan pembelajaran.
