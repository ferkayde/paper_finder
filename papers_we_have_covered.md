# Papers & Works We've Looked At

*Compiled June 13, 2026 — a running record of papers, books, and key results from our conversations, grouped by area. Probability theory is the spine; everything else hangs off it.*

---

## Probability Theory — Core

- **Williams, *Probability with Martingales*** (Cambridge) — flagged repeatedly as the best first read on martingales.
- **Durrett, *Probability: Theory and Examples*** (Cambridge) — measure-theoretic, Ch. 4 (random walks), Ch. 5 (martingales).
- **Grimmett & Stirzaker, *Probability and Random Processes*** (Oxford) — Ch. 12 martingales, strong problem sets.
- **Doob, *Stochastic Processes*** (Wiley, 1953) — the original martingale reference.
- **Resnick, *A Probability Path*** — computational companion.
- Optional Stopping Theorem, Doob decomposition, Azuma–Hoeffding, Gambler's Ruin, branching processes (worked through for the martingale talk).

## Stochastic Calculus & Brownian Motion

- **Mörters & Peres, *Brownian Motion*** (free online) — path properties, quadratic variation.
- **Øksendal, *Stochastic Differential Equations*** — concise Itô integral construction & Itô's lemma.
- **Shreve, *Stochastic Calculus for Finance I & II*** — Vol. II Ch. 3–5 the gold standard; Vol. I for the discrete/binomial bridge.
- **Mikosch, *Elementary Stochastic Calculus with Finance in View*.**
- **Feller, *An Introduction to Probability Theory and Its Applications, Vol. 1*** — Ch. 3 & 14, reflection principle / ballot problem.
- **Steele, *Stochastic Calculus and Financial Applications*** — bridges to finance.
- **Lawler**, stochastic calculus lecture notes (free).
- **Berestycki**, Cambridge lecture notes (free).
- Donsker's theorem, Itô's formula, Fokker–Planck, Feynman–Kac (the arc for the diffusion talk).

## Stein's Method & Approximation

- **84-page Stein's method survey** for beginning grad students (Normal/Poisson/Exponential/Geometric approximation, dependency graphs, exchangeable pairs, size-bias couplings).
- **Liu & Wang, "Stein Variational Gradient Descent: A General Purpose Bayesian Inference Algorithm,"** NeurIPS 2016 — arXiv:1608.04471.
- **Chen–Stein theorem** (Arratia, Goldstein & Gordon, 1989) — Poisson approximation for dependent indicators.
- Berry–Esseen (CLT convergence rate) — characteristic-function approach.

## Extreme Value Theory

- **Embrechts, Klüppelberg & Mikosch, *Modelling Extremal Events for Insurance and Finance*** (Springer, 1997) — the definitive reference.
- **Embrechts, Resnick & Samorodnitsky, "Extreme Value Theory as a Risk Management Tool,"** North American Actuarial Journal, 1999 (free PDF).
- **Bovier, "Extreme values of random processes,"** Univ. Bonn lecture notes (free).
- Pickands–Balkema–de Haan theorem, Peaks-over-Threshold, GEV/GPD.
- ML connections: OpenMax (CVPR 2016), Extreme Value Machine (Rudd et al., PAMI 2018), SPADE (2025).

## Diffusion / Score-Based Generative Models

- **Song et al., "Score-Based Generative Modeling through SDEs,"** 2021 — the Score SDE framework.
- **Lipman et al., "Flow Matching for Generative Modeling,"** 2022.
- **Song & Ermon (NCSN)** — denoising score matching.
- **Huang et al., "DiffusionPDE: Generative PDE-Solving Under Partial Observation,"** 2024 — arXiv:2406.17763.
- Follow-ups noted: EDM, DPM-Solver, consistency models.
- Probabilistic-method roots: Erdős's method, Lovász Local Lemma.

## Spectral Graph Theory

- **Gutman & Zhou, "Laplacian energy of a graph,"** 2006 — base paper for your Laplacian energy work.
- **Akbari–Hosseinzadeh conjecture** — E(G) ≥ Δ + δ for nonsingular graphs.
- **Arizmendi–Huerta**, 2024 — Randić index / energy connection.
- **So–Robbiano–de Abreu–Gutman**, 2010 — LE(G) ≥ E(G) for bipartite graphs (Ky Fan inequality).
- **Brouwer's conjecture** — equivalence to max Laplacian energy; Torres–Trevisan (2024), Lew (2025).
- **Haemers' Seidel-energy conjecture** (SE(G) ≥ 2n−2) — proved by Akbari et al., 2020.
- **AMCS — Adaptive Monte Carlo Search** (Vito et al., 2023) — arXiv:2306.07956, refuted 6 open conjectures.
- **Wagner**, 2021 — deep learning / cross-entropy for extremal combinatorics constructions.
- Signless Laplacian, distance, Seidel, Sombor, ABC energies; AutoGraphiX.

## Quantitative Finance

- **Gatev, Goetzmann & Rouwenhorst, "Pairs Trading: Performance of a Relative-Value Arbitrage Rule,"** 2006 — the EC581 replication paper.
- **Amihud, "Illiquidity and stock returns,"** 2002 — illiquidity ratio.
- **Roll's spread estimator**; **Corwin–Schultz** high-low estimator.
- **Engle–Granger** cointegration (used for the BIST cointegration vs distance comparison).
- **Black & Scholes, "The Pricing of Options and Corporate Liabilities,"** 1973.
- **Shiller, *Irrational Exuberance*** — CAPE ratio, excess volatility.
- HMM regime detection (Baum–Welch, Viterbi, forward algorithm) for markets.

## Signal Processing & Estimation (EE 372, lighter)

- Wiener filter / LMSE, MMSE / MAP / ML estimators, Bayesian estimation, M-PSK — coursework rather than papers, listed for completeness since they overlap your probability interests.

---

### How to use this file
Treat the bolded **keywords and author names** below as your starting "interest profile" for the weekly digest:

`probability theory · martingales · stochastic calculus · Brownian motion · SDEs ·
Stein's method · concentration inequalities · extreme value theory · large deviations ·
diffusion models · score-based generative models · spectral graph theory · graph energy ·
Laplacian eigenvalues · pairs trading · statistical arbitrage · cointegration · regime-switching`

Authors worth following directly: Gutman, Embrechts, Y. Song, Goldstein, plus your own group (Işlak).
