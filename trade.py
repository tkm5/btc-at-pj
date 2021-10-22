import requests
from datetime import datetime
from logging import getLogger, Formatter, StreamHandler, FileHandler, INFO
from pprint import pprint
import time
import numpy as np
import ccxt
import settings_secret

# -------------設定項目------------------------

wait = 180  # ループの待機時間
buy_term = 30  # 最高値（上値）ブレイクアウト期間
sell_term = 30  # 最安値（下値）ブレイクアウト期間
chart_sec = 3600  # 使用する時間軸（秒換算）
chart_API = "cryptowatch"  # 価格の取得元を（cryptowatch/cryptocompare）から選択

judge_price = {
    "BUY": "close_price",  # ブレイク判断　高値（high_price)か終値（close_price）を使用
    "SELL": "close_price"  # ブレイク判断　安値 (low_price)か終値（close_price）を使用
}

volatility_term = 5  # 平均ボラティリティの計算に使う期間
stop_range = 2  # 何レンジ幅にストップを入れるか
trade_risk = 0.03  # 1トレードあたり口座の何％まで損失を許容するか
levarage = 3  # レバレッジ倍率の設定

entry_times = 2  # 何回に分けて追加ポジションを取るか
entry_range = 1  # 何レンジごとに追加ポジションを取るか

trailing_config = "ON"  # ONで有効 OFFで無効
stop_AF = 0.02  # 加速係数
stop_AF_add = 0.02  # 加速係数を増やす度合
stop_AF_max = 0.2  # 加速係数の上限

filter_VER = "OFF"  # フィルター設定／OFFで無効
MA_term = 200  # トレンドフィルターに使う移動平均線の期間

bitflyer = ccxt.bitflyer()
bitflyer.apiKey = str(settings_secret.apikey)  # APIキーを設定
bitflyer.secret = str(settings_secret.secret)  # APIシークレットを設定
bitflyer.timeout = 30000  # 通信のタイムアウト時間の設定

line_config = "ON"  # LINE通知をするかどうかの設定
log_config = "OFF"  # ログファイルを出力するかの設定
log_file_path = ""  # ログを記録するファイル名と出力パス
line_token = str(settings_secret.line_token)  # LINE通知を使用する場合はAPIキーを入力

# -------------ログ機能の設定--------------------

# ログ機能の設定箇所
if log_config == "ON":
    logger = getLogger(__name__)
    handlerSh = StreamHandler()
    handlerFile = FileHandler(log_file_path)
    handlerSh.setLevel(INFO)
    handlerFile.setLevel(INFO)
    logger.setLevel(INFO)
    logger.addHandler(handlerSh)
    logger.addHandler(handlerFile)

# -------------注文管理の変数------------------------

flag = {
    "position": {
        "exist": False,
        "side": "",
        "price": 0,
        "stop": 0,
        "stop-AF": stop_AF,
        "stop-EP": 0,
        "ATR": 0,
        "lot": 0,
        "count": 0
    },
    "add-position": {
        "count": 0,
        "first-entry-price": 0,
        "last-entry-price": 0,
        "unit-range": 0,
        "unit-size": 0,
        "stop": 0
    }
}


# -------------売買ロジックの部分の関数--------------

# ドンチャンブレイクを判定する関数
def donchian(data, last_data):
    highest = max(i["high_price"] for i in last_data[(-1 * buy_term):])
    if data["settled"][judge_price["BUY"]] > highest:
        return {"side": "BUY", "price": highest}

    lowest = min(i["low_price"] for i in last_data[(-1 * sell_term):])
    if data["settled"][judge_price["SELL"]] < lowest:
        return {"side": "SELL", "price": lowest}

    return {"side": None, "price": 0}


# ドンチャンブレイクを判定してエントリー注文を出す関数
def entry_signal(data, last_data, flag):
    if flag["position"]["exist"] == True:
        return flag

    signal = donchian(data, last_data)
    if signal["side"] == "BUY":
        print_log("過去{0}足の最高値{1}円を、直近の価格が{2}円でブレイクしました".format(buy_term, signal["price"],
                                                               data["settled"][judge_price["BUY"]]))
        # フィルター条件を確認
        if filter(signal) == False:
            print_log("フィルターのエントリー条件を満たさなかったため、エントリーしません")
            return flag

        lot, stop, flag = calculate_lot(last_data, data, flag)
        if lot >= 0.01:
            print_log("{0}円あたりに{1}BTCで買いの成行注文を出します".format(data["settled"]["close_price"], lot))

            # ここに買い注文のコードを入れる
            price = bitflyer_market("BUY", lot)

            print_log("{0}円にストップを入れます".format(price - stop))
            flag["position"]["lot"], flag["position"]["stop"] = lot, stop
            flag["position"]["exist"] = True
            flag["position"]["side"] = "BUY"
            flag["position"]["price"] = price
        else:
            print_log("注文可能枚数{}が、最低注文単位に満たなかったので注文を見送ります".format(lot))

    if signal["side"] == "SELL":
        print_log("過去{0}足の最安値{1}円を、直近の価格が{2}円でブレイクしました".format(sell_term, signal["price"],
                                                               data["settled"][judge_price["SELL"]]))
        # フィルター条件を確認
        if filter(signal) == False:
            print_log("フィルターのエントリー条件を満たさなかったため、エントリーしません")
            return flag

        lot, stop, flag = calculate_lot(last_data, data, flag)
        if lot >= 0.01:
            print_log("{0}円あたりに{1}BTCの売りの成行注文を出します".format(data["settled"]["close_price"], lot))

            # ここに売り注文のコードを入れる
            price = bitflyer_market("SELL", lot)

            print_log("{0}円にストップを入れます".format(price + stop))
            flag["position"]["lot"], flag["position"]["stop"] = lot, stop
            flag["position"]["exist"] = True
            flag["position"]["side"] = "SELL"
            flag["position"]["price"] = price
        else:
            print_log("注文可能枚数{}が、最低注文単位に満たなかったので注文を見送ります".format(lot))

    return flag


# 損切ラインにかかったら成行注文で決済する関数
def stop_position(data, flag):
    # トレイリングストップを実行
    if trailing_config == "ON":
        flag = trail_stop(data, flag)

    if flag["position"]["side"] == "BUY":
        stop_price = flag["position"]["price"] - flag["position"]["stop"]
        if data["forming"]["low_price"] < stop_price:
            print_log("{0}円の損切ラインに引っかかりました。".format(stop_price))
            print_log(str(data["forming"]["low_price"]) + "円あたりで成行注文を出してポジションを決済します")

            # 決済の成行注文コードを入れる
            bitflyer_market("SELL", flag["position"]["lot"])

            flag["position"]["exist"] = False
            flag["position"]["count"] = 0
            flag["position"]["stop-AF"] = stop_AF
            flag["position"]["stop-EP"] = 0
            flag["add-position"]["count"] = 0

    if flag["position"]["side"] == "SELL":
        stop_price = flag["position"]["price"] + flag["position"]["stop"]
        if data["forming"]["high_price"] > stop_price:
            print_log("{0}円の損切ラインに引っかかりました。".format(stop_price))
            print_log(str(data["forming"]["high_price"]) + "円あたりで成行注文を出してポジションを決済します")

            # 決済の成行注文コードを入れる
            bitflyer_market("BUY", flag["position"]["lot"])

            flag["position"]["exist"] = False
            flag["position"]["count"] = 0
            flag["position"]["stop-AF"] = stop_AF
            flag["position"]["stop-EP"] = 0
            flag["add-position"]["count"] = 0

    return flag


# 手仕舞いのシグナルが出たら決済の成行注文 + ドテン注文 を出す関数
def close_position(data, last_data, flag):
    if flag["position"]["exist"] == False:
        return flag

    flag["position"]["count"] += 1
    signal = donchian(data, last_data)

    if flag["position"]["side"] == "BUY":
        if signal["side"] == "SELL":
            print_log("過去{0}足の最安値{1}円を、直近の価格が{2}円でブレイクしました".format(sell_term, signal["price"],
                                                                   data["settled"][judge_price["SELL"]]))
            print_log(str(data["settled"]["close_price"]) + "円あたりで成行注文を出してポジションを決済します")

            # 決済の成行注文コードを入れる
            bitflyer_market("SELL", flag["position"]["lot"])

            flag["position"]["exist"] = False
            flag["position"]["count"] = 0
            flag["position"]["stop-AF"] = stop_AF
            flag["position"]["stop-EP"] = 0
            flag["add-position"]["count"] = 0

            # ドテン注文の箇所
            # フィルター条件を確認
            if filter(signal) == False:
                print_log("フィルターのエントリー条件を満たさなかったため、エントリーしません")
                return flag

            lot, stop, flag = calculate_lot(last_data, data, flag)
            if lot >= 0.01:
                print_log("さらに{0}円あたりに{1}BTCの売りの成行注文を入れてドテン出します".format(data["settled"]["close_price"], lot))

                # ここに売り注文のコードを入れる
                price = bitflyer_market("SELL", lot)

                print_log("{0}円にストップを入れます".format(price + stop))
                flag["position"]["lot"], flag["position"]["stop"] = lot, stop
                flag["position"]["exist"] = True
                flag["position"]["side"] = "SELL"
                flag["position"]["price"] = price

    if flag["position"]["side"] == "SELL":
        if signal["side"] == "BUY":
            print_log("過去{0}足の最高値{1}円を、直近の価格が{2}円でブレイクしました".format(buy_term, signal["price"],
                                                                   data["settled"][judge_price["BUY"]]))
            print_log(str(data["settled"]["close_price"]) + "円あたりで成行注文を出してポジションを決済します")

            # 決済の成行注文コードを入れる
            bitflyer_market("BUY", flag["position"]["lot"])

            flag["position"]["exist"] = False
            flag["position"]["count"] = 0
            flag["position"]["stop-AF"] = stop_AF
            flag["position"]["stop-EP"] = 0
            flag["add-position"]["count"] = 0

            # ドテン注文の箇所
            # フィルター条件を確認
            if filter(signal) == False:
                print_log("フィルターのエントリー条件を満たさなかったため、エントリーしません")
                return flag

            lot, stop, flag = calculate_lot(last_data, data, flag)
            if lot >= 0.01:
                print_log("さらに{0}円あたりで{1}BTCの買いの成行注文を入れてドテンします".format(data["settled"]["close_price"], lot))

                # ここに買い注文のコードを入れる
                price = bitflyer_market("BUY", lot)

                print_log("{0}円にストップを入れます".format(price - stop))
                flag["position"]["lot"], flag["position"]["stop"] = lot, stop
                flag["position"]["exist"] = True
                flag["position"]["side"] = "BUY"
                flag["position"]["price"] = price

    return flag


# -------------トレンドフィルターの関数--------------

# トレンドフィルターの関数
def filter(signal):
    if filter_VER == "OFF":
        return True

    if filter_VER == "A":
        if len(last_data) < MA_term:
            return True
        if data["settled"]["close_price"] > calculate_MA(MA_term) and signal["side"] == "BUY":
            return True
        if data["settled"]["close_price"] < calculate_MA(MA_term) and signal["side"] == "SELL":
            return True

    if filter_VER == "B":
        if len(last_data) < MA_term:
            return True
        if calculate_MA(MA_term) > calculate_MA(MA_term, -1) and signal["side"] == "BUY":
            return True
        if calculate_MA(MA_term) < calculate_MA(MA_term, -1) and signal["side"] == "SELL":
            return True
    return False


# 単純移動平均を計算する関数
def calculate_MA(value, before=None):
    if before is None:
        MA = sum(i["close_price"] for i in last_data[-1 * value:]) / value
    else:
        MA = sum(i["close_price"] for i in last_data[-1 * value + before: before]) / value
    return round(MA)


# -------------資金管理の関数--------------

# 注文ロットを計算する関数
def calculate_lot(last_data, data, flag):
    # 口座残高を取得する
    balance = bitflyer_collateral()

    # 最初のエントリーの場合
    if flag["add-position"]["count"] == 0:
        # １回の注文単位（ロット数）と、追加ポジの基準レンジを計算する
        volatility = calculate_volatility(last_data)
        stop = stop_range * volatility
        calc_lot = np.floor(balance * trade_risk / stop * 100) / 100

        flag["add-position"]["unit-size"] = np.floor(calc_lot / entry_times * 100) / 100
        flag["add-position"]["unit-range"] = round(volatility * entry_range)
        flag["add-position"]["stop"] = stop
        flag["position"]["ATR"] = round(volatility)

        print_log("現在のアカウント残高は{}円です".format(balance))
        print_log("許容リスクから購入できる枚数は最大{}BTCまでです".format(calc_lot))
        print_log("{0}回に分けて{1}BTCずつ注文します".format(entry_times, flag["add-position"]["unit-size"]))

    # ストップ幅には、最初のエントリー時に計算したボラティリティを使う
    stop = flag["add-position"]["stop"]

    # 実際に購入可能な枚数を計算する
    able_lot = np.floor(balance * levarage / data["forming"]["close_price"] * 100) / 100
    lot = min(able_lot, flag["add-position"]["unit-size"])

    print_log("証拠金から購入できる枚数は最大{}BTCまでです".format(able_lot))
    return lot, stop, flag


# 複数回に分けて追加ポジションを取る関数
def add_position(data, flag):
    # ポジションがない場合は何もしない
    if flag["position"]["exist"] == False:
        return flag

    # 最初（１回目）のエントリー価格を記録
    if flag["add-position"]["count"] == 0:
        flag["add-position"]["first-entry-price"] = flag["position"]["price"]
        flag["add-position"]["last-entry-price"] = flag["position"]["price"]
        flag["add-position"]["count"] += 1

    # 以下の場合は、追加ポジションを取らない
    if flag["add-position"]["count"] >= entry_times:
        return flag

    # この関数の中で使う変数を用意
    first_entry_price = flag["add-position"]["first-entry-price"]
    last_entry_price = flag["add-position"]["last-entry-price"]
    unit_range = flag["add-position"]["unit-range"]
    current_price = data["forming"]["close_price"]

    # 価格がエントリー方向に基準レンジ分だけ進んだか判定する
    should_add_position = False
    if flag["position"]["side"] == "BUY" and (current_price - last_entry_price) > unit_range:
        should_add_position = True
    elif flag["position"]["side"] == "SELL" and (last_entry_price - current_price) > unit_range:
        should_add_position = True

    # 基準レンジ分進んでいれば追加注文を出す
    if should_add_position == True:
        print_log(
            "前回のエントリー価格{0}円からブレイクアウトの方向に{1}ATR（{2}円）以上動きました".format(last_entry_price, entry_range, round(unit_range)))
        print_log("{0}/{1}回目の追加注文を出します".format(flag["add-position"]["count"] + 1, entry_times))

        # 注文サイズを計算
        lot, stop, flag = calculate_lot(last_data, data, flag)
        if lot < 0.01:
            print_log("注文可能枚数{}が、最低注文単位に満たなかったので注文を見送ります".format(lot))
            flag["add-position"]["count"] += 1
            return flag

        # 追加注文を出す
        if flag["position"]["side"] == "BUY":
            # ここに買い注文のコードを入れる
            print_log("現在のポジションに追加して{}BTCの買い注文を出します".format(lot))
            entry_price = bitflyer_market("BUY", lot)

        if flag["position"]["side"] == "SELL":
            # ここに売り注文のコードを入れる
            print_log("現在のポジションに追加して{}BTCの売り注文を出します".format(lot))
            entry_price = bitflyer_market("SELL", lot)

        # ポジション全体の情報を更新する
        flag["position"]["stop"] = stop
        flag["position"]["price"] = int(round(
            (flag["position"]["price"] * flag["position"]["lot"] + entry_price * lot) / (
                        flag["position"]["lot"] + lot)))
        flag["position"]["lot"] = np.round((flag["position"]["lot"] + lot) * 100) / 100

        if flag["position"]["side"] == "BUY":
            print_log("{0}円の位置にストップを更新します".format(flag["position"]["price"] - stop))
        elif flag["position"]["side"] == "SELL":
            print_log("{0}円の位置にストップを更新します".format(flag["position"]["price"] + stop))
        print_log("現在のポジションの取得単価は{}円です".format(flag["position"]["price"]))
        print_log("現在のポジションサイズは{}BTCです".format(flag["position"]["lot"]))

        flag["add-position"]["count"] += 1
        flag["add-position"]["last-entry-price"] = entry_price

    return flag


# トレイリングストップの関数
def trail_stop(data, flag):
    # まだ追加ポジションの取得中であれば何もしない
    if flag["add-position"]["count"] < entry_times:
        return flag

    # 高値／安値がエントリー価格からいくら離れたか計算
    if flag["position"]["side"] == "BUY":
        moved_range = round(data["settled"]["high_price"] - flag["position"]["price"])
    if flag["position"]["side"] == "SELL":
        moved_range = round(flag["position"]["price"] - data["settled"]["low_price"])

    # 最高値・最安値を更新したか調べる
    if moved_range < 0 or flag["position"]["stop-EP"] >= moved_range:
        return flag
    else:
        flag["position"]["stop-EP"] = moved_range

    # 加速係数に応じて損切りラインを動かす
    flag["position"]["stop"] = round(
        flag["position"]["stop"] - (moved_range + flag["position"]["stop"]) * flag["position"]["stop-AF"])

    # 加速係数を更新
    flag["position"]["stop-AF"] = round(flag["position"]["stop-AF"] + stop_AF_add, 2)
    if flag["position"]["stop-AF"] >= stop_AF_max:
        flag["position"]["stop-AF"] = stop_AF_max

    # ログ出力
    if flag["position"]["side"] == "BUY":
        print_log("トレイリングストップの発動：ストップ位置を{}円に動かして、加速係数を{}に更新します".format(
            round(flag["position"]["price"] - flag["position"]["stop"]), flag["position"]["stop-AF"]))
    else:
        print_log("トレイリングストップの発動：ストップ位置を{}円に動かして、加速係数を{}に更新します".format(
            round(flag["position"]["price"] + flag["position"]["stop"]), flag["position"]["stop-AF"]))

    return flag


# -------------価格APIの関数--------------

# BTCFXのチャート価格をAPIで取得する関数（実行時の取得用）
def get_price(min, before=0, after=0):
    # Cryptowatchを使用する場合
    if chart_API == "cryptowatch":
        price = []
        params = {"periods": min}
        if before != 0:
            params["before"] = before
        if after != 0:
            params["after"] = after

        response = requests.get("https://api.cryptowat.ch/markets/bitflyer/btcfxjpy/ohlc", params)
        data = response.json()

        if data["result"][str(min)] is not None:
            for i in data["result"][str(min)]:
                if i[1] != 0 and i[2] != 0 and i[3] != 0 and i[4] != 0:
                    price.append({"close_time": i[0],
                                  "close_time_dt": datetime.fromtimestamp(i[0]).strftime('%Y/%m/%d %H:%M'),
                                  "open_price": i[1],
                                  "high_price": i[2],
                                  "low_price": i[3],
                                  "close_price": i[4]})
            return price

        else:
            print_log("データが存在しません")
            return None

    # CryptoCompareを使用する場合（１時間足のみ対応）
    if chart_API == "cryptocompare":
        price = []
        params = {"fsym": "BTC", "tsym": "JPY", "e": "bitflyerfx", "limit": 2000}

        response = requests.get("https://min-api.cryptocompare.com/data/histohour", params, timeout=10)
        data = response.json()

        if data["Response"] == "Success":
            for i in data["Data"]:
                price.append({"close_time": i["time"],
                              "close_time_dt": datetime.fromtimestamp(i["time"]).strftime('%Y/%m/%d %H:%M'),
                              "open_price": i["open"],
                              "high_price": i["high"],
                              "low_price": i["low"],
                              "close_price": i["close"]})
            return price

        else:
            print_log("データが存在しません")
            return None


# BTCFXのチャート価格をAPIで取得する関数（リアルタイム用）
def get_realtime_price(min):
    # Cryptowatchを使用する場合
    if chart_API == "cryptowatch":
        params = {"periods": min}
        while True:
            try:
                response = requests.get("https://api.cryptowat.ch/markets/bitflyer/btcfxjpy/ohlc", params, timeout=10)
                response.raise_for_status()
                data = response.json()
                return {
                    "settled": {
                        "close_time": data["result"][str(min)][-2][0],
                        "open_price": data["result"][str(min)][-2][1],
                        "high_price": data["result"][str(min)][-2][2],
                        "low_price": data["result"][str(min)][-2][3],
                        "close_price": data["result"][str(min)][-2][4]
                    },
                    "forming": {"close_time": data["result"][str(min)][-1][0],
                                "open_price": data["result"][str(min)][-1][1],
                                "high_price": data["result"][str(min)][-1][2],
                                "low_price": data["result"][str(min)][-1][3],
                                "close_price": data["result"][str(min)][-1][4]
                                }
                }

            except requests.exceptions.RequestException as e:
                print_log("Cryptowatchの価格取得でエラー発生 : " + str(e))
                print_log("{}秒待機してやり直します".format(wait))
                time.sleep(wait)

    # CryptoCompareを使用する場合（１時間足のみ対応）
    if chart_API == "cryptocompare":
        params = {"fsym": "BTC", "tsym": "JPY", "e": "bitflyerfx"}

        while True:
            try:
                response = requests.get("https://min-api.cryptocompare.com/data/histohour", params, timeout=10)
                response.raise_for_status()
                data = response.json()
                time.sleep(5)

                response2 = requests.get("https://min-api.cryptocompare.com/data/histominute", params, timeout=10)
                response2.raise_for_status()
                data2 = response2.json()

            except requests.exceptions.RequestException as e:
                print_log("Cryptocompareの価格取得でエラー発生 : " + str(e))
                print_log("{}秒待機してやり直します".format(wait))
                time.sleep(wait)
                continue

            return {
                "settled": {
                    "close_time": data["Data"][-2]["time"],
                    "open_price": data["Data"][-2]["open"],
                    "high_price": data["Data"][-2]["high"],
                    "low_price": data["Data"][-2]["low"],
                    "close_price": data["Data"][-2]["close"]
                },
                "forming": {
                    "close_time": data2["Data"][-1]["time"],
                    "open_price": data2["Data"][-1]["open"],
                    "high_price": data2["Data"][-1]["high"],
                    "low_price": data2["Data"][-1]["low"],
                    "close_price": data2["Data"][-1]["close"]
                }
            }


# -------------その他の補助関数--------------

# 時間と高値・安値・終値を表示する関数
def print_price(data):
    print_log("時間： " + datetime.fromtimestamp(data["close_time"]).strftime('%Y/%m/%d %H:%M') + " 高値： " + str(
        data["high_price"]) + " 安値： " + str(data["low_price"]) + " 終値： " + str(data["close_price"]))


# １期間の平均ボラティリティを計算する
def calculate_volatility(last_data):
    high_sum = sum(i["high_price"] for i in last_data[-1 * volatility_term:])
    low_sum = sum(i["low_price"] for i in last_data[-1 * volatility_term:])
    volatility = round((high_sum - low_sum) / volatility_term)
    print_log("現在の{0}期間の平均ボラティリティは{1}円です".format(volatility_term, volatility))
    return volatility


# ログファイルの出力やLINE通知の関数
def print_log(text):
    # LINE通知する場合
    if line_config == "ON":
        url = "https://notify-api.line.me/api/notify"
        data = {"message": str(text)}
        headers = {"Authorization": "Bearer " + line_token}
        try:
            requests.post(url, data=data, headers=headers)
        except requests.exceptions.RequestException as e:
            if log_config == "ON":
                logger.info(str(e))
            else:
                print(str(e))

    # コマンドラインへの出力とファイル保存
    if log_config == "ON":
        logger.info(text)
    else:
        print(text)


# -------------トラブル対策用の関数--------------

def find_unexpected_pos(flag):
    if flag["position"]["exist"] == True:
        return flag
    count = 0
    while True:
        price, size, side = bitflyer_check_positions()
        if size == 0:
            return flag

        print_log("把握していないポジションが見つかりました")
        print_log("反映の遅延でないことを確認するため様子を見ています")
        count += 1

        if count > 5:
            print_log("把握していないポジションが見つかったためポジションを復活させます")

            flag["position"]["exist"] = True
            flag["position"]["side"] = side
            flag["position"]["lot"] = size
            flag["position"]["price"] = price
            flag["position"]["stop-AF"] = stop_AF
            flag["position"]["stop-EP"] = 0
            flag["add-position"]["count"] = entry_times

            if flag["position"]["ATR"] == 0:
                flag["position"]["ATR"] = calculate_volatility(last_data)
                flag["position"]["stop"] = flag["position"]["ATR"] * stop_range
            pprint(flag)
            return flag
        time.sleep(30)


# -------------Bitflyerと通信する関数--------------

# 成行注文をする関数
def bitflyer_market(side, lot):
    while True:
        try:
            order = bitflyer.create_order(
                symbol='BTC/JPY',
                type='market',
                side=side,
                amount=lot,
                params={"product_code": "FX_BTC_JPY"})
            print_log("--------------------")
            print_log(order)
            print_log("--------------------")
            order_id = order["id"]
            time.sleep(30)

            # 執行状況を確認
            average_price = bitflyer_check_market_order(order_id, lot)
            return average_price

        except ccxt.BaseError as e:
            print_log("Bitflyerの注文APIでエラー発生 : " + str(e))
            print_log("注文が失敗しました")
            print_log("30秒待機してやり直します")
            time.sleep(30)


# 成行注文の執行状況を確認する関数
def bitflyer_check_market_order(id, lot):
    while True:
        try:
            size = []
            price = []

            executions = bitflyer.private_get_getexecutions(params={"product_code": "FX_BTC_JPY"})
            for exec in executions:
                if exec["child_order_acceptance_id"] == id:
                    size.append(exec["size"])
                    price.append(exec["price"])

            # 全部約定するまで待つ
            if round(sum(size), 2) != lot:
                time.sleep(20)
                print_log("注文がすべて約定するのを待っています")
            else:
                # 平均価格を計算する
                average_price = round(sum(price[i] * size[i] for i in range(len(price))) / sum(size))
                print_log("すべての成行注文が執行されました")
                print_log("執行価格は平均 {}円です".format(average_price))
                return average_price

        except ccxt.BaseError as e:
            print_log("BitflyerのAPIで問題発生 : " + str(e))
            print_log("20秒待機してやり直します")
            time.sleep(20)


# 口座残高を取得する関数
def bitflyer_collateral():
    while True:
        try:
            collateral = bitflyer.private_get_getcollateral()
            spendable_collateral = np.floor(collateral["collateral"] - collateral["require_collateral"])
            print_log("現在のアカウント残高は{}円です".format(int(collateral["collateral"])))
            print_log("新規注文に利用可能な証拠金の額は{}円です".format(int(spendable_collateral)))
            return int(spendable_collateral)

        except ccxt.BaseError as e:
            print_log("BitflyerのAPIでの口座残高取得に失敗しました ： " + str(e))
            print_log("20秒待機してやり直します")
            time.sleep(20)


# ポジション情報を取得する関数
def bitflyer_check_positions():
    failed = 0
    while True:
        try:
            size = []
            price = []
            positions = bitflyer.private_get_getpositions(params={"product_code": "FX_BTC_JPY"})
            if not positions:
                # print_log("現在ポジションは存在しません")
                return 0, 0, None
            for pos in positions:
                size.append(pos["size"])
                price.append(pos["price"])
                side = pos["side"]

            # 平均建値を計算する
            average_price = round(sum(price[i] * size[i] for i in range(len(price))) / sum(size))
            sum_size = round(sum(size), 2)
            # print_log("保有中の建玉：合計{}つ\n平均建値：{}円\n合計サイズ：{}BTC\n方向：{}".format(len(price),average_price,sum_size,side))

            # 価格・サイズ・方向を返す
            return average_price, sum_size, side

        except ccxt.BaseError as e:
            failed += 1
            if failed > 10:
                print_log("Bitflyerのポジション取得APIでエラーに10回失敗しました : " + str(e))
                print_log("20秒待機してやり直します")
            time.sleep(20)


# ------------ここからメイン処理の記述--------------

# 最低限、保持が必要なローソク足の期間を準備

need_term = max(buy_term, sell_term, volatility_term, MA_term)
print_log("{}期間分のデータの準備中".format(need_term))

price = get_price(chart_sec)
last_data = price[-1 * need_term - 2:-2]
print_price(last_data[-1])
print_log("--{}秒待機--".format(wait))
time.sleep(wait)

print_log("---実行開始---")

while True:

    # 最新のローソク足を取得して表示
    data = get_realtime_price(chart_sec)
    if data["settled"]["close_time"] > last_data[-1]["close_time"]:
        print_price(data["settled"])

    # ポジションがある場合
    if flag["position"]["exist"]:
        flag = stop_position(data, flag)
        flag = close_position(data, last_data, flag)
        flag = add_position(data, flag)

    # ポジションがない場合
    else:
        flag = find_unexpected_pos(flag)
        flag = entry_signal(data, last_data, flag)

    # 確定足が更新された場合
    if data["settled"]["close_time"] > last_data[-1]["close_time"]:
        last_data.append(data["settled"])
        if len(last_data) > need_term:
            del last_data[0]

    time.sleep(wait)
