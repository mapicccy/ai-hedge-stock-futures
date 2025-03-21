import os
import pandas as pd
import requests
import akshare as ak
import datetime

from data.cache import get_cache
from data.models import (
    CompanyNews,
    CompanyNewsResponse,
    FinancialMetrics,
    FinancialMetricsResponse,
    Price,
    PriceResponse,
    LineItem,
    LineItemResponse,
    InsiderTrade,
    InsiderTradeResponse,
)

# Global cache instance
_cache = get_cache()


def get_prices(ticker: str, assets: str, start_date: str, end_date: str, realtime: bool=False) -> list[Price]:
    """Fetch price data from cache or API."""
    start_date = start_date.replace("-", "")
    end_date = end_date.replace("-", "")
    # Check cache first
    if not realtime:
        if cached_data := _cache.get_prices(ticker):
            # Filter cached data by date range and convert to Price objects
            filtered_data = [Price(**price) for price in cached_data if start_date <= price["time"] <= end_date]
            if filtered_data:
                return filtered_data

    # If not in cache or no data in range, fetch from API
    if assets == "A":
        df = ak.stock_zh_a_hist(ticker, start_date="20150401", end_date=end_date, adjust="qfq")
        df.rename(columns={"日期": "trade_date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "vol", "成交额": "amount", "涨跌幅": "pct_chg", "涨跌额": "change", "换手率": "turn_over"}, inplace=True)
        df = df.sort_index(ascending=True)
    elif assets == "US":
        df = ak.stock_us_daily(symbol=ticker, adjust="qfq")
        df = df[df['date'] <= pd.to_datetime(end_date)]
        df = df[df['date'] >= pd.to_datetime(start_date)]
        df.rename(columns={"date": "trade_date", "volume": "vol"}, inplace=True)
        df = df.reset_index(drop=True)
    else:
        df = ak.futures_zh_minute_sina(symbol=ticker, period=15)
        df.rename(columns={"datetime": "trade_date", "volume": "vol", "成交额": "amount"}, inplace=True)
        df = df.loc[df['trade_date'] <= end_date + " 23:59:00"]
        df['trade_date'] = pd.to_datetime(df['trade_date'], errors='coerce', format='%Y-%m-%d %H:%M:%S')
        df = df.sort_index(ascending=True)

    if df.empty:
        raise Exception(f"Error fetching data: {ticker} - {end_date}")

    prices = [
        Price(
            time=row['trade_date'].strftime("%Y%m%d%H%M%S") if assets != "A" and assets != "US" else row['trade_date'].strftime("%Y%m%d"),
            open=row['open'],
            high=row['high'],
            low=row['low'],
            close=row['close'],
            volume=row['vol']
        )
        for _, row in df.iterrows()
    ]
    if not realtime:
        # Cache the results as dicts
        _cache.set_prices(ticker, [p.model_dump() for p in prices])
    return prices


def get_financial_metrics(
    ticker: str,
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
) -> list[FinancialMetrics]:
    return []

    """Fetch financial metrics from cache or API."""
    # Check cache first
    if cached_data := _cache.get_financial_metrics(ticker):
        # Filter cached data by date and limit
        filtered_data = [FinancialMetrics(**metric) for metric in cached_data if metric["report_period"] <= end_date]
        filtered_data.sort(key=lambda x: x.report_period, reverse=True)
        if filtered_data:
            return filtered_data[:limit]

    # If not in cache or insufficient data, fetch from API
    headers = {}
    if api_key := os.environ.get("FINANCIAL_DATASETS_API_KEY"):
        headers["X-API-KEY"] = api_key

    url = f"https://api.financialdatasets.ai/financial-metrics/?ticker={ticker}&report_period_lte={end_date}&limit={limit}&period={period}"
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception(f"Error fetching data: {ticker} - {response.status_code} - {response.text}")

    # Parse response with Pydantic model
    metrics_response = FinancialMetricsResponse(**response.json())
    # Return the FinancialMetrics objects directly instead of converting to dict
    financial_metrics = metrics_response.financial_metrics

    if not financial_metrics:
        return []

    # Cache the results as dicts
    _cache.set_financial_metrics(ticker, [m.model_dump() for m in financial_metrics])
    return financial_metrics


def search_line_items(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
) -> list[LineItem]:
    """Fetch line items from API."""
    return []

    # If not in cache or insufficient data, fetch from API
    headers = {}
    if api_key := os.environ.get("FINANCIAL_DATASETS_API_KEY"):
        headers["X-API-KEY"] = api_key

    url = "https://api.financialdatasets.ai/financials/search/line-items"

    body = {
        "tickers": [ticker],
        "line_items": line_items,
        "end_date": end_date,
        "period": period,
        "limit": limit,
    }
    response = requests.post(url, headers=headers, json=body)
    if response.status_code != 200:
        raise Exception(f"Error fetching data: {ticker} - {response.status_code} - {response.text}")
    data = response.json()
    response_model = LineItemResponse(**data)
    search_results = response_model.search_results
    if not search_results:
        return []

    # Cache the results
    return search_results[:limit]


def get_insider_trades(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> list[InsiderTrade]:
    return []

    """Fetch insider trades from cache or API."""
    # Check cache first
    if cached_data := _cache.get_insider_trades(ticker):
        # Filter cached data by date range
        filtered_data = [InsiderTrade(**trade) for trade in cached_data 
                        if (start_date is None or (trade.get("transaction_date") or trade["filing_date"]) >= start_date)
                        and (trade.get("transaction_date") or trade["filing_date"]) <= end_date]
        filtered_data.sort(key=lambda x: x.transaction_date or x.filing_date, reverse=True)
        if filtered_data:
            return filtered_data

    # If not in cache or insufficient data, fetch from API
    headers = {}
    if api_key := os.environ.get("FINANCIAL_DATASETS_API_KEY"):
        headers["X-API-KEY"] = api_key

    all_trades = []
    current_end_date = end_date
    
    while True:
        url = f"https://api.financialdatasets.ai/insider-trades/?ticker={ticker}&filing_date_lte={current_end_date}"
        if start_date:
            url += f"&filing_date_gte={start_date}"
        url += f"&limit={limit}"
        
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            raise Exception(f"Error fetching data: {ticker} - {response.status_code} - {response.text}")
        
        data = response.json()
        response_model = InsiderTradeResponse(**data)
        insider_trades = response_model.insider_trades
        
        if not insider_trades:
            break
            
        all_trades.extend(insider_trades)
        
        # Only continue pagination if we have a start_date and got a full page
        if not start_date or len(insider_trades) < limit:
            break
            
        # Update end_date to the oldest filing date from current batch for next iteration
        current_end_date = min(trade.filing_date for trade in insider_trades).split('T')[0]
        
        # If we've reached or passed the start_date, we can stop
        if current_end_date <= start_date:
            break

    if not all_trades:
        return []

    # Cache the results
    _cache.set_insider_trades(ticker, [trade.model_dump() for trade in all_trades])
    return all_trades


def get_company_news(
    ticker: str,
    assets: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> list[CompanyNews]:
    """Fetch company news from cache or API."""
    # Check cache first
    if cached_data := _cache.get_company_news(ticker):
        # Filter cached data by date range
        filtered_data = [CompanyNews(**news) for news in cached_data 
                        if (start_date is None or news["date"] >= start_date)
                        and news["date"] <= end_date]
        filtered_data.sort(key=lambda x: x.date, reverse=True)
        if filtered_data:
            return filtered_data

    # If not in cache or insufficient data, fetch from API
    if assets == "A":
        df = ak.stock_news_em(symbol=ticker)
        df.rename(columns={"关键词": "ticker", "新闻标题": "title", "文章来源": "author", "新闻内容": "source", "发布时间": "date", "新闻链接": "url"}, inplace=True)
        df = df.loc[df['date'] <= end_date + " 23:59:59"]
        print(df)
    elif assets == "US":
        return []
    else:
        return []

    if df.empty:
        raise Exception(f"Error fetching data: {ticker} - {end_date}")

    all_news = [
        CompanyNews(
            ticker=row['ticker'],
            date=pd.to_datetime(row['date']).strftime("%Y%m%d%H%M%S"),
            title=row['title'],
            author=row['author'],
            source=row['source'],
            url=row['url'],
        )
        for _, row in df.iterrows()
    ]

    # Cache the results
    _cache.set_company_news(ticker, [news.model_dump() for news in all_news])
    return all_news


def get_market_cap(
    ticker: str,
    end_date: str,
) -> float | None:
    """Fetch market cap from the API."""
    financial_metrics = get_financial_metrics(ticker, end_date)
    if not financial_metrics:
        return None
    market_cap = financial_metrics[0].market_cap
    if not market_cap:
        return None

    return market_cap


def prices_to_df(prices: list[Price]) -> pd.DataFrame:
    """Convert prices to a DataFrame."""
    df = pd.DataFrame([p.model_dump() for p in prices])
    df["Date"] = pd.to_datetime(df["time"])
    df.set_index("Date", inplace=True)
    numeric_cols = ["open", "close", "high", "low", "volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.sort_index(inplace=True)
    return df


# Update the get_price_data function to use the new functions
def get_price_data(ticker: str, assets: str, start_date: str, end_date: str, realtime: bool = False) -> pd.DataFrame:
    prices = get_prices(ticker, assets, start_date, end_date, realtime)
    return prices_to_df(prices)
