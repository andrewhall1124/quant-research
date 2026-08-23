from thetadata import ThetaClient
from datetime import date

client = ThetaClient(dataframe_type='polars')

symbols = client.stock_list_symbols()

dates = client.stock_list_dates(request_type='quote', symbol=['AAPL'])

df = client.stock_history_eod(
    symbol='AAPL',
    start_date=date(2024, 1, 1),
    end_date=date(2024, 1, 31),
)

print(df)