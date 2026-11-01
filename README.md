# 武蔵野大学 Web サイトのサイトマップ抽出

`www.musashino-u.ac.jp` のトップページから同一ホスト上の HTML リンクを幅優先で巡回し、URL と `<title>` の内容を `sitemap.csv` に保存します。

## 実行方法

Python 3 があれば追加パッケージなしで実行できます。

```bash
python3 scrape_site.py --delay 1.0 --output sitemap.csv
```

各HTTPリクエストの直前に `time.sleep(delay)` を実行します。既定の待機時間は1秒、上限は2,000ページです。ページ数を増やす場合は `--max-pages`、待機時間は `--delay` で指定できます。CSVはExcelで開きやすいUTF-8 BOM付きで、列は `URL,title` です。

## 注意

URLは `www.musashino-u.ac.jp` のホストに限定します。PDFなどHTML以外のファイルはCSVに含めません。サイトの構成や応答状況によって、到達できるページ数は変わります。上限に到達した場合は未訪問URL数を表示します。
