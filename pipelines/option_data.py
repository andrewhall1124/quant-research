from thetadata import ThetaClient
from datetime import date

client = ThetaClient(dataframe_type='polars')

symbols = client.option_list_symbols()

dates = client.option_list_dates(
    request_type='quote',
    symbol='AAPL',
    expiration=date(2022, 9, 30),
)

expirations = client.option_list_expirations(symbol=['AAPL'])

strikes = client.option_list_strikes(symbol=['AAPL'], expiration=date(2022, 9, 30))

df = client.option_history_eod(
    start_date=date(2024, 11, 4),
    end_date=date(2024, 11, 4),
    symbol='AAPL',
    expiration=date(2024, 11, 15),
)

print(df)



