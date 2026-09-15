# Codex Limits

`coli` は Codex の現在の使用量と reset credits を `~/.codex/auth.json` の認証情報で取得し、terminal 向けに併記するコマンドです。

## セットアップ

Python 3.10以降を使用します。依存はPython標準ライブラリのみです。
OSに `Asia/Tokyo` のタイムゾーンデータが必要です（macOSでは標準搭載）。
ChatGPTアカウントでCodexにログインし、`~/.codex/auth.json` に `tokens.access_token` が保存されている環境を対象とします。
APIキー認証のみの環境には対応していません。

```sh
ghq get kazuhideoki/codex-limits
cd "$(ghq root)/github.com/kazuhideoki/codex-limits"
python3 codex_limits.py
```

ghqを使わない場合は `git clone https://github.com/kazuhideoki/codex-limits.git` で取得できます。
dotfilesの `coli` ラッパーは、このghq配置先の `codex_limits.py` を実行します。
単独で `coli` コマンドを使う場合は、PATH内のディレクトリにスクリプトへのリンクを作成してください。

## Usage

```sh
coli
```

terminal 出力では、5時間枠/週間枠のリセットまでの残り時間率、現在の使用量の残量パーセント、reset credit の期限までの残日数をバーで表示します。
5時間枠の残り時間率は、リセット直後が `100%`、リセット直前が `0%` に近くなります。
週間枠の `利用時間残り (09-20)` は、JSTの毎日09:00〜20:00だけを対象に、リセットまでに残っている利用時間の割合を表示します。20:00〜翌09:00の間は減少しません。
reset credit は既定で `30日 = 100%` として計算します。
reset credit の残り時間は1時間未満になると分単位で表示し、1日未満の行を warning、2時間未満の行を error の色で表示します。

既定では次の endpoint を呼び出します。

```text
https://chatgpt.com/backend-api/codex/usage
https://chatgpt.com/backend-api/wham/rate-limit-reset-credits
```

`codex/usage` は非公開の ChatGPT backend endpoint です。
`auth.json` の token は出力しません。

引数は受け付けません。認証ファイルの場所は `~/.codex/auth.json` に固定です。
`CODEX_HOME` を変えている環境では、現在の実装はその変更を参照しません。

## APIと認証情報

OpenAI公式のツールではありません。非公開のChatGPT backend endpointを使用するため、
APIや認証形式の変更で動かなくなる可能性があります。
認証トークンはローカルファイルから読み、上記のChatGPT endpointへ送信します。
使用量とreset creditsの取得はGETのみで、creditの使用や購入は行いません。
認証ファイルや実際のAPIレスポンスを、このリポジトリへ追加しないでください。

## 検証

認証情報やネットワーク接続なしで実行できます。

```sh
python3 -m unittest discover -v
```

元の実装は [dotfiles](https://github.com/kazuhideoki/dotfiles) の `scripts/codex_limits` から移植しました。

## ライセンス

MIT。詳細は [LICENSE](LICENSE) を参照してください。
