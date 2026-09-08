# Reading list: signal ideas for the volatility book

Papers whose signals could feed the voltium level book or the planned
earnings, term-structure and skew books. Grouped by the part of the system
they touch. Papers already replicated or relied on in `research/` and
`voltium/` are marked **[replicated]** or **[used]**. Citations are from
memory; check the journal and year against the DOI before quoting them.

For each paper: authors (year), title, venue, and the one idea to pull out.

## 1. Cross-section of single-name option returns (the level book)

| Authors (year) | Title | Venue | Idea for the system |
| --- | --- | --- | --- |
| Goyal and Saretto (2009) **[replicated]** | Cross-Section of Option Returns and Volatility | JFE | log(HV/IV) straddle sort; the baseline. 2008 draft used in `research/goyal_saretto`. |
| Cao and Han (2013) **[replicated]** | Cross Section of Option Returns and Idiosyncratic Stock Volatility | JFE | Delta-hedged returns fall in idio vol; motivates the idio-vol control and the `sigma_idio` scaling. |
| Coval and Shumway (2001) | Expected Option Returns | JF | Why zero-beta straddles lose money on average; the sign of the premium the book is harvesting. |
| Bakshi and Kapadia (2003a) | Delta-Hedged Gains and the Negative Market Volatility Risk Premium | RFS | Delta-hedged gains as the measure of the vol premium; the reference-return construction. |
| Bakshi and Kapadia (2003b) | Volatility Risk Premiums Embedded in Individual Equity Options: Some New Insights | Journal of Derivatives | Single-name premium is smaller than the index's and loads on market vol; the market-vol factor in the risk model. |
| Zhan, Cao, Han and Tong (2022) | Option Return Predictability | RFS | Stock characteristics (cash-to-assets, profit margin, issuance, analyst dispersion, ...) predict delta-hedged option returns. A menu of alphas to residualise or stack. |
| Bali, Beckmeyer, Moerke and Weigert (2023) | Option Return Predictability with Machine Learning and Big Data | RFS | Nonlinear combination of option- and stock-level characteristics; which features matter for delta-hedged returns. |
| Goyenko and Zhang (2022) | The Joint Cross Section of Option and Stock Returns Predictability with Big Data | working paper (RFS forthcoming) | Option-implied features predicting both legs; a feature list to mine. |
| Heston, Jones, Khorram, Li and Mo (2023) **[replicated]** | Option Momentum | JF | Past delta-hedged straddle returns predict future ones (momentum at 1–12 months, reversal at 1 month). A pure time-series alpha on the reference returns already stored. `research/vol_momentum` and `research/vol_reversal`. |
| Hu and Jacobs (2020) | Volatility and Expected Option Returns | JFQA | Underlying vol level predicts call and put returns with opposite signs; the vol level as a control, not just a signal. |
| Aretz, Lin and Poon (2023) | Moneyness, Underlying Asset Volatility, and the Cross-Section of Option Returns | Review of Finance | How vol and moneyness interact; relevant to the ATM selection and re-strike rule. |
| Vasquez and Xiao (2024) | Default Risk and Option Returns | Management Science | Distress predicts low delta-hedged returns; a credit-based control. |
| Ramachandran and Tayal (2021) | Mispricing, Short-Sale Constraints, and the Cross-Section of Option Returns | JFE | Overpriced, hard-to-short stocks have expensive options; a short-constraint signal. |
| Boyer and Vorkink (2014) | Stock Options as Lotteries | JF | Ex-ante skewness of the option payoff predicts low returns; lottery demand as a richness signal. |
| Byun and Kim (2016) | Gambling Preference and Individual Equity Option Returns | JFE | Lottery-like underlyings have overpriced calls. |
| Karakaya (2014) | Characteristics and Expected Returns in Individual Equity Options | working paper | Level, slope and value factors in single-name option returns. |
| Frazzini and Pedersen (2022) | Embedded Leverage | Review of Asset Pricing Studies | Higher embedded leverage, lower option returns; argues for which strike to trade. |
| Christoffersen, Goyenko, Jacobs and Karoui (2018) | Illiquidity Premia in the Equity Options Market | RFS | Illiquid options earn a premium for the seller; an illiquidity signal and a fill-cost prior. |
| Muravyev and Pearson (2020) | Options Trading Costs Are Lower than You Think | RFS | Effective spreads are far below quoted when timed to the underlying; the half-spread assumption killing the net P&L. |
| Muravyev (2016) | Order Flow and Expected Option Returns | JF | Inventory-driven order flow predicts short-horizon option returns; execution timing. |
| Jones and Shemesh (2018) | Option Mispricing Around Nontrading Periods | JF | Options are overpriced into weekends and holidays; roll and rebalance cadence. |

## 2. Volatility forecasting (the HAR side of the VRP)

| Authors (year) | Title | Venue | Idea for the system |
| --- | --- | --- | --- |
| Corsi (2009) **[used]** | A Simple Approximate Long-Memory Model of Realized Volatility | Journal of Financial Econometrics | The HAR in `HARForecaster`. |
| Yang and Zhang (2000) **[used]** | Drift-Independent Volatility Estimation Based on High, Low, Open, and Close Prices | Journal of Business | The OHLC estimator in `compute_realized_vol_panel`. |
| Andersen, Bollerslev, Diebold and Labys (2003) | Modeling and Forecasting Realized Volatility | Econometrica | Log-RV is near-Gaussian and long-memory; why the HAR is fit in logs. |
| Bollerslev, Patton and Quaedvlieg (2016) | Exploiting the Errors: A Simple Approach for Improved Volatility Forecasting | Journal of Econometrics | HARQ: shrink the daily-RV coefficient by its measurement error. |
| Patton and Sheppard (2015) | Good Volatility, Bad Volatility: Signed Jumps and the Persistence of Volatility | Review of Economics and Statistics | Split RV into up and down semivariance; negative semivariance forecasts better. |
| Christensen and Prabhala (1998) | The Relation Between Implied and Realized Volatility | JFE | IV is a biased but informative forecast; combine IV and RV in the forecast rather than treating IV as the target only. |
| Poon and Granger (2003) | Forecasting Volatility in Financial Markets: A Review | Journal of Economic Literature | Survey of what beats what. |
| Ni, Pan and Poteshman (2008) | Volatility Information Trading in the Option Market | JF | Non-market-maker vega demand predicts realized vol; volume and OI as forecast inputs. |

## 3. Variance risk premium: level, sign and timing

| Authors (year) | Title | Venue | Idea for the system |
| --- | --- | --- | --- |
| Carr and Wu (2009) **[replicated in spirit]** | Variance Risk Premiums | RFS | Synthetic variance swap rate minus realized variance; the premium measured in `research/variance_risk_premium`. |
| Bollerslev, Tauchen and Zhou (2009) | Expected Stock Returns and Variance Risk Premia | RFS | Index VRP predicts market returns; a regime input for scaling the book. |
| Driessen, Maenhout and Vilkov (2009) **[cited]** | The Price of Correlation Risk: Evidence from Equity Options | JF | Index vol is rich relative to single names; the dispersion trade and the market-vol hedge. |
| Buss and Vilkov (2012) | Measuring Equity Risk with Option-Implied Correlations | RFS | Option-implied correlation as a factor; a cleaner market-vol loading. |
| Cheng (2019) | The VIX Premium | RFS | The VIX futures premium falls before vol spikes; a timing signal for the short-vol tilt. |
| Johnson (2017) **[replicated in spirit]** | Risk Premia and the VIX Term Structure | JFQA | Slope of the VIX curve prices variance risk; `research/vix_term_structure`. |
| Simon and Campasano (2014) | The VIX Futures Basis: Evidence and Trading Strategies | Journal of Derivatives | The contango carry rule. |
| Dew-Becker, Giglio, Le and Rodriguez (2017) | The Price of Variance Risk | JFE | Only the front of the variance term structure carries a premium; where on the curve to sell. |
| Egloff, Leippold and Wu (2010) | The Term Structure of Variance Swap Rates and Optimal Variance Swap Investments | JFQA | Two-factor variance term structure and how to allocate across tenors. |
| Aït-Sahalia, Karaman and Mancini (2020) | The Term Structure of Equity and Variance Risk Premia | Journal of Econometrics | Term structure of the premium over horizons. |
| Moreira and Muir (2017) | Volatility-Managed Portfolios | JF | Scale exposure by inverse recent variance; a sizing rule for gross vega. |
| Israelov and Tummala (2017) | Which Index Options Should You Sell? | working paper (AQR) | Where on the index surface the premium per unit of risk is largest. |

## 4. Term structure of single-name IV (the calendar book)

| Authors (year) | Title | Venue | Idea for the system |
| --- | --- | --- | --- |
| Vasquez (2017) **[replicated]** | Equity Volatility Term Structures and the Cross Section of Option Returns | JFE | Steep upward slope predicts high straddle returns; `research/iv_skew_slope`. |
| Stein (1989) | Overreactions in the Options Market | JF | Long-dated IV overreacts to short-dated moves; the original term-structure mean-reversion signal. |
| Poteshman (2001) | Underreaction, Overreaction, and Increasing Misreaction to Information in the Options Market | JF | Reaction to vol news depends on the length of the news run; conditioning the slope signal on recent IV changes. |
| Jones and Wang (2012) | The Term Structure of Equity Option Implied Volatility | working paper | Slope predicts option returns and the direction of IV changes. |
| Andries, Eisenbach, Schmalz and Wang (2015) | The Term Structure of the Price of Variance Risk | working paper | Horizon-dependent risk aversion and the shape of the variance premium curve. |

## 5. Skew and risk-neutral moments (the skew book)

| Authors (year) | Title | Venue | Idea for the system |
| --- | --- | --- | --- |
| Xing, Zhang and Zhao (2010) **[replicated]** | What Does the Individual Option Volatility Smirk Tell Us About Future Equity Returns? | JFQA | OTM put minus ATM call IV predicts stock returns; `research/iv_skew_slope`. |
| Bakshi, Kapadia and Madan (2003) | Stock Return Characteristics, Skew Laws, and the Differential Pricing of Individual Equity Options | RFS | Model-free risk-neutral variance, skew and kurtosis from the chain; the moment calculators for the skew book. |
| Bali and Murray (2013) | Does Risk-Neutral Skewness Predict the Cross-Section of Equity Option Portfolio Returns? | JFQA | Skewness assets (long OTM call, short OTM put, delta- and vega-neutral) earn less when skew is high; the risk reversal as the traded unit. |
| Conrad, Dittmar and Ghysels (2013) | Ex Ante Skewness and Expected Stock Returns | JF | Risk-neutral skew and vol predict stock returns. |
| Stilger, Kostakis and Poon (2017) | What Does Risk-Neutral Skewness Tell Us About Future Stock Returns? | Management Science | Skew-return relation runs through short-sale constraints. |
| Dennis and Mayhew (2002) | Risk-Neutral Skewness: Evidence from Stock Options | JFQA | What drives cross-sectional skew (beta, size, leverage); the residualisation for a skew signal. |
| Kozhan, Neuberger and Schneider (2013) | The Skew Risk Premium in the Equity Index Market | RFS | Skew swaps; the skew premium is the same risk as the variance premium. |
| Cremers and Weinbaum (2010) | Deviations from Put-Call Parity and Stock Return Predictability | JFQA | Call minus put IV at the same strike predicts stock returns; an informed-flow signal available from the chain. |
| Bali and Hovakimian (2009) | Volatility Spreads and Expected Stock Returns | Management Science | Call-put IV spread and the realized-implied spread as stock-return predictors. |
| An, Ang, Bali and Cakici (2014) | The Joint Cross Section of Stocks and Options | JF | Changes in call and put IV predict stock returns; IV changes as a hedge-side signal. |
| Bollen and Whaley (2004) | Does Net Buying Pressure Affect the Shape of Implied Volatility Functions? | JF | Demand moves the smile; skew as a flow footprint that reverts. |
| Gârleanu, Pedersen and Poteshman (2009) | Demand-Based Option Pricing | RFS | Dealer inventory prices options; open interest by strike as a richness signal. |
| Schneider, Wagner and Zechner (2020) | Low-Risk Anomalies? | JF | Skewness explains low-vol anomalies; links the skew book to the equity sibling. |
| Duan and Wei (2009) | Systematic Risk and the Price Structure of Individual Equity Options | RFS | Higher systematic risk share raises IV level and steepens the smile; beta as a control for both books. |

## 6. Volatility of volatility, tails and jumps

| Authors (year) | Title | Venue | Idea for the system |
| --- | --- | --- | --- |
| Ruan (2020) | Volatility-of-Volatility and the Cross-Section of Option Returns | Journal of Financial Markets | High vol-of-vol names have low delta-hedged returns; a second moment of the IV series as a signal. |
| Cao, Vasquez, Xiao and Zhan (2023) | Why Does Volatility Uncertainty Predict Equity Option Returns? | Quarterly Journal of Finance | Mechanism behind the vol-of-vol effect. |
| Baltussen, Van Bekkum and Van der Grient (2018) | Unknown Unknowns: Uncertainty About Risk and Stock Returns | JFQA | Vol-of-vol as a stock-return predictor. |
| Kelly and Jiang (2014) | Tail Risk and Asset Prices | RFS | Cross-sectional tail-risk estimate; tail exposure as a control. |
| Bollerslev and Todorov (2011) | Tails, Fears, and Risk Premia | JF | Separating jump fear from diffusive variance in the premium. |
| Bali, Cakici and Whitelaw (2011) | Maxing Out: Stocks as Lotteries and the Cross-Section of Expected Returns | JFE | Recent extreme daily returns; the `gap_freq` feature has a literature. |
| Ang, Hodrick, Xing and Zhang (2006) | The Cross-Section of Volatility and Expected Returns | JF | Idio vol and stock returns; background for the Cao-Han control. |

## 7. Earnings and event timing (the earnings adjuster)

| Authors (year) | Title | Venue | Idea for the system |
| --- | --- | --- | --- |
| Dubinsky, Johannes, Kaeck and Seeger (2019) | Option Pricing of Earnings Announcement Risks | RFS | Jump-variance decomposition of the term structure around earnings; the formula in `docs/extending.md`. |
| Gao, Xing and Zhang (2018) | Anticipating Uncertainty: Straddles Around Earnings Announcements | JFQA | Straddles bought before the run-up earn positive returns; `research/earnings_straddles` run-up window. |
| Barth and So (2014) | Non-Diversifiable Volatility Risk and Risk Premiums at Earnings Announcements | The Accounting Review | Which announcements carry a vol premium. |
| Atilgan (2014) | Volatility Spreads and Earnings Announcement Returns | Journal of Banking and Finance | Call-put IV spread before the print predicts the announcement return. |
| Jin, Livnat and Zhang (2012) | Option Prices Leading Equity Prices: Do Option Traders Have an Information Advantage? | Journal of Accounting Research | Skew and spread changes before earnings. |
| Pan and Poteshman (2006) | The Information in Option Volume for Future Stock Prices | RFS | Put-call volume ratios predict stock returns; requires signed volume. |
| Roll, Schwartz and Subrahmanyam (2010) | O/S: The Relative Trading Activity in Options and Stock | JFE | Option-to-stock volume around announcements. |
| Johnson and So (2012) | The Option to Stock Volume Ratio and Future Returns | JFE | O/S predicts stock returns; usable from the store's volume columns. |

## 8. Factor structure and risk model

| Authors (year) | Title | Venue | Idea for the system |
| --- | --- | --- | --- |
| Büchner and Kelly (2022) | A Factor Model for Option Returns | JFE | IPCA factors for option returns; a characteristic-based risk model instead of PCA on reference returns. |
| Horenstein, Vasquez and Xiao (2020) | Common Factors in Equity Option Returns | working paper | Level, market-vol and idio-vol factors in delta-hedged returns. |
| Cont and da Fonseca (2002) | Dynamics of Implied Volatility Surfaces | Quantitative Finance | PCA of surface moves (level, slope, skew); the factor set for a multi-book risk model. |
| Jiang and Tian (2005) | The Model-Free Implied Volatility and Its Information Content | RFS | Model-free IV construction; a better `iv30` than the ATM pillar. |
| Britten-Jones and Neuberger (2000) | Option Prices, Implied Price Processes, and Stochastic Volatility | JF | The variance-swap replication behind model-free IV. |
| Chen, Joslin and Ni (2019) | Demand for Crash Insurance, Intermediary Constraints, and Risk Premia in Financial Markets | RFS | Dealer put demand as a state variable for the premium. |

## Suggested order

1. Zhan, Cao, Han and Tong (2022); Heston, Jones, Khorram, Li and Mo (2023); Bali, Beckmeyer, Moerke and Weigert (2023). The three most direct sources of new level-book alphas.
2. Muravyev and Pearson (2020); Christoffersen, Goyenko, Jacobs and Karoui (2018); Jones and Shemesh (2018). The cost side, which is what the seven-year book loses on.
3. Dubinsky, Johannes, Kaeck and Seeger (2019). The earnings adjuster.
4. Bakshi, Kapadia and Madan (2003); Bali and Murray (2013); Stein (1989). The skew and term books.
5. Bollerslev, Patton and Quaedvlieg (2016); Patton and Sheppard (2015). Cheap HAR upgrades.
6. Büchner and Kelly (2022); Cont and da Fonseca (2002). The risk model once there is more than one book.
