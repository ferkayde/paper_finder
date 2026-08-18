#!/usr/bin/env python3
"""
arxiv_digest.py — weekly arXiv digest, weighted toward probability theory.

Pure standard library. No third-party dependencies.

Pulls recent papers from chosen arXiv categories, scores each one against a
keyword/author interest profile, then optionally asks Claude to select the
best papers based on your reading history and interest profile, and add
one-line notes. Falls back to score-based selection if no API key is set.

Usage:
    python arxiv_digest.py --dry-run            # print to stdout, no email
    python arxiv_digest.py --days 7             # look back 7 days (default)
    python arxiv_digest.py                      # fetch + email

Environment variables:
    SMTP_USER           full gmail address
    SMTP_PASS           gmail app password
    MAIL_TO             recipient(s), comma-separated for multiple
    ANTHROPIC_API_KEY   (optional) enables Claude-based selection and notes
"""

import argparse
import datetime as dt
import html
import json
import os
import random
import re
import smtplib
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ──────────────────────────────────────────────────────────────────────────
# CONFIG — tune this freely. This is your "interest profile".
# ──────────────────────────────────────────────────────────────────────────

CATEGORIES = {
    "math.PR": 1.0,   # Probability  ← core
    "math.ST": 0.6,   # Statistics theory
    "math.SP": 0.5,   # Spectral theory
    "math.CO": 0.5,   # Combinatorics (spectral graph theory lives partly here)
    "q-fin.ST": 0.5,  # Statistical finance
    "q-fin.TR": 0.5,  # Trading & market microstructure
    "q-fin.MF": 0.45, # Mathematical finance
    "stat.ML": 0.4,   # Machine learning (diffusion / score-based models)
    "cs.LG": 0.35,    # Learning
    "math.DS": 0.35,
}

PROB_CATEGORIES = {"math.PR", "math.ST"}

KEYWORDS = {
    # probability core
    "martingale": 3.0, "stochastic": 1.5, "brownian": 2.5, "sde": 2.0,
    "stochastic differential": 2.5, "ito": 2.0, "fokker-planck": 2.5,
    "stein's method": 4.0, "stein method": 4.0, "concentration inequalit": 3.0,
    "large deviation": 3.0, "extreme value": 3.0, "heavy tail": 2.0,
    "central limit": 2.0, "berry-esseen": 3.5, "coupling": 1.5,
    "markov chain": 2.0, "mixing time": 2.5, "random walk": 2.0,
    "random matrix": 2.0, "poisson approximation": 3.0,
    # spectral graph theory
    "graph energy": 2.0, "laplacian energy": 1.5, "laplacian eigenvalue": 1.5,
    "spectral graph": 2.0, "graph theoery": 3.0, "random graph": 3.0,
    # diffusion / generative
    "diffusion model": 2.5, "score-based": 2.0, "score matching": 2.0,
    "flow matching": 2.0, "generative model": 1.5, "stochastic interpolant": 3.0,
    # quant finance
    "pairs trading": 4.0, "statistical arbitrage": 3.5, "cointegration": 3.0,
    "mean reversion": 2.5, "regime-switching": 3.0, "regime switching": 3.0,
    "market microstructure": 2.0, "illiquidity": 2.5, "limit order book": 2.0,
}

AUTHORS = {
    "islak", "işlak",
    "haggstrom", "häggström",
    "gutman", "embrechts", "goldstein"
}

CATEGORY_BIAS = 2.0
TITLE_WEIGHT = 3.0
ABSTRACT_WEIGHT = 1.0
AUTHOR_BONUS = 8.0

# Final selection quotas.
N_PROB = 3              # new papers from PROB_CATEGORIES
N_OTHER = 2              # new papers from other categories
N_CLASSIC = 1            # curated older papers per week
MIN_SCORE = 0.0

# Candidate pool sizes fed to Claude (must be >= N_PROB / N_OTHER).
CANDIDATE_PROB = 20
CANDIDATE_OTHER = 10

# Seen-papers cache — prevents cross-week repeats.
SEEN_CACHE = "seen_papers.json"
SEEN_MAX_WEEKS = 12      # keep IDs this long
SEEN_RECENT_WEEKS = 4    # titles within this window are sent to Claude as context

# Curated list of important older arXiv IDs to rotate through.
# N_CLASSIC are chosen per ISO week. Add IDs here to grow the pool.
CLASSIC_PAPERS = [
    # ── Probability / random matrices ─────────────────────────────────────
    "math/9904022",  # Schramm (1999) — Scaling limits of LERW & uniform spanning trees (introduced SLE)
    "math/0112234",  # Lawler, Schramm, Werner (2001) — Conformal invariance of planar LERW & UST
    "0809.2643",     # Chelkak & Smirnov (2008) — Loop-erased random walk and Poisson kernel on planar graphs
    "1007.0575",     # Duminil-Copin & Smirnov (2010) — Connective constant of the honeycomb lattice
    "0906.0510",     # Tao & Vu (2010) — Random matrices: universality of local eigenvalue statistics
    "0912.0966",     # Tao & Vu (2011) — Random covariance matrices: universality of local statistics
    "1109.3041",     # Decelle et al. (2011) — Stochastic block model for modular networks
    # ── Stein's method / normal approximation ─────────────────────────────
    "math/0611213",  # Chatterjee (2006) — A new method of normal approximation
    "1109.1880",     # Ross (2011) — Fundamentals of Stein's Method (survey)
    "1404.1392",     # Chatterjee (2014) — A short survey of Stein's method
    "1608.04471",    # Liu & Wang (2016) — Stein Variational Gradient Descent
    # ── Markov chains / mixing times / MCMC ───────────────────────────────
    "math/0305349",  # Morris & Peres (2003) — Evolving sets, mixing and heat kernel bounds
    "1108.0133",     # Peres & Sousi (2011) — Mixing times are hitting times of large sets
    "1111.4246",     # Hoffman & Gelman (2011) — The No-U-Turn Sampler (NUTS)
    "1402.4102",     # Chen, Fox, Guestrin (2014) — Stochastic Gradient Hamiltonian Monte Carlo
    "1907.06986",    # Nemeth & Fearnhead (2019) — Stochastic gradient MCMC (review)
    # ── Random graphs ─────────────────────────────────────────────────────
    "math/0504589",  # Bollobás, Janson, Riordan (2007) — Phase transition in inhomogeneous random graphs
    # ── Stochastic PDEs / regularity structures ────────────────────────────
    "1109.6811",     # Hairer (2012) — Solving the KPZ equation
    "1210.2684",     # Gubinelli, Imkeller, Perkowski (2012) — Paracontrolled distributions & singular PDEs
    "1303.5113",     # Hairer (2014) — A theory of regularity structures
    "1508.05261",    # Hairer & Quastel (2015) — Regularity structures and the dynamical Φ⁴₃ model
    "2208.02492",    # Gu & Komorowski (2022) — An invariance principle for the 1D KPZ equation
    # ── Quantitative finance / SDEs ────────────────────────────────────────
    "1410.3394",     # Gatheral, Jaisson, Rosenbaum (2018) — Volatility is rough
    "1706.04702",    # Han, Jentzen, E (2018) — Deep learning for high-dimensional BSDEs
    "1707.02568",    # E, Han, Jentzen (2017) — Deep BSDE: solving high-dimensional PDEs
    "1802.03042",    # Buehler et al. (2019) — Deep Hedging
    # ── Spectral graph theory ──────────────────────────────────────────────
    "0803.0929",     # Spielman & Srivastava (2008) — Graph Sparsification by Effective Resistances
    "2306.07956",    # Vito et al. (2023) — AMCS: refuted 6 open conjectures in spectral graph theory
    # ── Score-based / diffusion / generative models ────────────────────────
    "1312.6114",     # Kingma & Welling (2013) — Auto-Encoding Variational Bayes (VAE)
    "1406.2661",     # Goodfellow et al. (2014) — Generative Adversarial Nets
    "1701.07875",    # Arjovsky et al. (2017) — Wasserstein GAN
    "1806.07366",    # Chen et al. (2018) — Neural Ordinary Differential Equations
    "1907.05600",    # Song & Ermon (2019) — Generative Modeling by Estimating Gradients (NCSN)
    "2006.11239",    # Ho et al. (2020) — Denoising Diffusion Probabilistic Models (DDPM)
    "2011.13456",    # Song et al. (2021) — Score-Based Generative Modeling through SDEs
    "2112.10752",    # Rombach et al. (2022) — Latent Diffusion Models (Stable Diffusion)
    "2206.00364",    # Karras et al. (2022) — Elucidating the Design Space of Diffusion Models (EDM)
    "2209.15571",    # Albergo & Vanden-Eijnden (2022) — Stochastic Interpolants
    "2210.02747",    # Lipman et al. (2022) — Flow Matching for Generative Modeling
    # ── Diffusion for PDEs ────────────────────────────────────────────────
    "2406.17763",    # Huang et al. (2024) — DiffusionPDE
    # ── Rough paths / stochastic volatility ───────────────────────────────
    "1609.02108",    # El Euch & Rosenbaum (2016) — Characteristic function of rough Heston models
    "1710.07481",    # Bayer, Friz, Gassiat et al. (2017) — A regularity structure for rough volatility
    "2210.01214",    # Chong, Hoffmann, Liu, Rosenbaum (2022) — Statistical inference for rough volatility: Minimax Theory
    "1801.06416",    # Abi Jaber, El Euch, Pulido, Rosenbaum (2019) — Affine forward variance models
    # ── Normalizing flows / variational inference ──────────────────────────
    "1505.05770",    # Rezende & Mohamed (2015) — Variational Inference with Normalizing Flows

    # ── Pre-arXiv classics (full metadata dicts — no arXiv ID) ────────────
    # Format: title, authors, year, summary, abs_url (DOI), pdf_url (open mirror).
    # abs_url and pdf_url can be the same if a separate PDF link isn't available.
    {
        "title": "Stochastic Integral",
        "authors": ["Kiyosi Itô"],
        "year": 1944,
        "summary": "Introduces integration with respect to Brownian motion — the Itô integral. "
                   "Foundational paper of stochastic calculus; everything from SDEs to the "
                   "Black-Scholes equation rests on it.",
        "abs_url": "https://doi.org/10.3792/pia/1195572786",
        "pdf_url": "https://projecteuclid.org/euclid.pja/1195572786",
        "categories": ["math.PR"],
    },
    {
        "title": "A Stochastic Approximation Method",
        "authors": ["Herbert Robbins", "Sutton Monro"],
        "year": 1951,
        "summary": "Introduces stochastic gradient descent — a noisy but convergent root-finding "
                   "procedure. The algorithmic ancestor of essentially all modern first-order "
                   "optimizers in machine learning.",
        "abs_url": "https://doi.org/10.1214/aoms/1177729586",
        "pdf_url": "https://projecteuclid.org/euclid.aoms/1177729586",
        "categories": ["math.PR", "stat.ML"],
    },
    {
        "title": "Equation of State Calculations by Fast Computing Machines",
        "authors": ["N. Metropolis", "A. W. Rosenbluth", "M. N. Rosenbluth", "A. H. Teller", "E. Teller"],
        "year": 1953,
        "summary": "Introduces the Metropolis algorithm — the first MCMC sampler. Proposes "
                   "accept/reject moves calibrated to an energy ratio, making it possible to "
                   "sample from distributions known only up to a constant.",
        "abs_url": "https://doi.org/10.1063/1.1699114",
        "pdf_url": "https://doi.org/10.1063/1.1699114",
        "categories": ["math.PR", "stat.CO"],
    },
    {
        "title": "On the Evolution of Random Graphs",
        "authors": ["Paul Erdős", "Alfréd Rényi"],
        "year": 1960,
        "summary": "Establishes the sharp connectivity threshold for G(n,p) at p = log(n)/n "
                   "and describes the dramatic phase transition in giant-component emergence. "
                   "Founded the probabilistic method in combinatorics.",
        "abs_url": "http://www.renyi.hu/~p_erdos/1960-10.pdf",
        "pdf_url": "http://www.renyi.hu/~p_erdos/1960-10.pdf",
        "categories": ["math.PR", "math.CO"],
    },
    {
        "title": "On Distributions of Certain Wiener Functionals",
        "authors": ["Mark Kac"],
        "year": 1949,
        "summary": "Derives the Feynman-Kac formula, connecting Brownian motion expectations to "
                   "solutions of parabolic PDEs. The bridge between probability theory and "
                   "the theory of heat equations.",
        "abs_url": "https://doi.org/10.1090/S0002-9947-1949-0027960-X",
        "pdf_url": "https://doi.org/10.1090/S0002-9947-1949-0027960-X",
        "categories": ["math.PR", "math.AP"],
    },
    {
        "title": "Über die analytischen Methoden in der Wahrscheinlichkeitsrechnung",
        "authors": ["Andrei N. Kolmogorov"],
        "year": 1931,
        "summary": "Kolmogorov's 1931 paper establishing the analytical foundations of Markov "
                   "processes via forward and backward equations (the Kolmogorov equations). "
                   "Gave probability theory its first rigorous PDE underpinning.",
        "abs_url": "https://doi.org/10.1007/BF01457949",
        "pdf_url": "https://doi.org/10.1007/BF01457949",
        "categories": ["math.PR"],
    },
    # ── Probability theory ────────────────────────────────────────────────
    {
        "title": "On the Movement of Small Particles Suspended in a Stationary Liquid",
        "authors": ["Albert Einstein"],
        "year": 1905,
        "summary": "The mathematical explanation of Brownian motion: derives that a suspended "
                   "particle undergoes diffusion with variance proportional to time, giving "
                   "the first rigorous prediction of the diffusion coefficient.",
        "abs_url": "https://doi.org/10.1002/andp.19053220806",
        "pdf_url": "https://doi.org/10.1002/andp.19053220806",
        "categories": ["math.PR"],
    },
    {
        "title": "Differential-Space",
        "authors": ["Norbert Wiener"],
        "year": 1923,
        "summary": "Constructs Wiener measure — a rigorous probability measure on the space of "
                   "continuous functions — giving the first mathematical existence proof for "
                   "Brownian motion as a stochastic process.",
        "abs_url": "https://doi.org/10.1002/sapm192321131",
        "pdf_url": "https://doi.org/10.1002/sapm192321131",
        "categories": ["math.PR"],
    },
    {
        "title": "Eine neue Herleitung des Exponentialgesetzes in der Wahrscheinlichkeitsrechnung",
        "authors": ["Jarl Waldemar Lindeberg"],
        "year": 1922,
        "summary": "Proves the central limit theorem under the Lindeberg condition — a necessary "
                   "and sufficient criterion for convergence to the Gaussian — replacing the "
                   "classical i.i.d. assumption.",
        "abs_url": "https://doi.org/10.1007/BF01494395",
        "pdf_url": "https://doi.org/10.1007/BF01494395",
        "categories": ["math.PR"],
    },
    {
        "title": "On Information and Sufficiency",
        "authors": ["Solomon Kullback", "Richard A. Leibler"],
        "year": 1951,
        "summary": "Introduces the Kullback-Leibler divergence as a measure of information "
                   "lost when one distribution is used to approximate another. Fundamental to "
                   "information theory, Bayesian inference, and modern ML loss functions.",
        "abs_url": "https://doi.org/10.1214/aoms/1177729694",
        "pdf_url": "https://projecteuclid.org/euclid.aoms/1177729694",
        "categories": ["math.PR", "math.ST"],
    },
    {
        "title": "La prévision : ses lois logiques, ses sources subjectives",
        "authors": ["Bruno de Finetti"],
        "year": 1937,
        "summary": "Introduces de Finetti's theorem on exchangeability: an infinite sequence of "
                   "exchangeable random variables is a mixture of i.i.d. sequences. "
                   "Foundational to Bayesian probability.",
        "abs_url": "https://www.numdam.org/item/AIHP_1937__7_1_1_0/",
        "pdf_url": "https://www.numdam.org/item/AIHP_1937__7_1_1_0/",
        "categories": ["math.PR"],
    },
    {
        "title": "The Fundamental Limit Theorems in Probability",
        "authors": ["William Feller"],
        "year": 1945,
        "summary": "A masterful survey by Feller synthesizing the law of large numbers, "
                   "central limit theorems, and iterated logarithm, setting the stage for "
                   "his landmark two-volume treatise.",
        "abs_url": "https://doi.org/10.1090/S0002-9904-1945-08448-1",
        "pdf_url": "https://doi.org/10.1090/S0002-9904-1945-08448-1",
        "categories": ["math.PR"],
    },
    {
        "title": "Regularity Properties of Certain Families of Chance Variables",
        "authors": ["Joseph L. Doob"],
        "year": 1940,
        "summary": "Establishes that every martingale has a càdlàg modification — a "
                   "right-continuous version with left limits. The regularity theorem that "
                   "underpins modern martingale theory.",
        "abs_url": "https://doi.org/10.1090/S0002-9947-1940-0002052-6",
        "pdf_url": "https://doi.org/10.1090/S0002-9947-1940-0002052-6",
        "categories": ["math.PR"],
    },
    {
        "title": "The Gaussian Law of Errors in the Theory of Additive Number Theoretic Functions",
        "authors": ["Paul Erdős", "Marc Kac"],
        "year": 1940,
        "summary": "Shows that the number of distinct prime factors of a random integer satisfies "
                   "the CLT — a striking bridge between probability and analytic number theory. "
                   "Founded probabilistic number theory.",
        "abs_url": "https://doi.org/10.2307/2371483",
        "pdf_url": "https://doi.org/10.2307/2371483",
        "categories": ["math.PR", "math.NT"],
    },
    {
        "title": "Distribution of Eigenvalues for Some Sets of Random Matrices",
        "authors": ["Vladimir A. Marchenko", "Leonid A. Pastur"],
        "year": 1967,
        "summary": "Derives the Marchenko-Pastur law — the limiting spectral distribution of "
                   "sample covariance matrices as dimensions grow proportionally. "
                   "The free-probability counterpart of the Wigner semicircle.",
        "abs_url": "https://doi.org/10.1070/SM1967v001n04ABEH001994",
        "pdf_url": "https://doi.org/10.1070/SM1967v001n04ABEH001994",
        "categories": ["math.PR"],
    },
    # ── Stochastic processes / SDEs ───────────────────────────────────────
    {
        "title": "On Stochastic Differential Equations",
        "authors": ["Kiyosi Itô"],
        "year": 1951,
        "summary": "Develops the full theory of stochastic differential equations driven by "
                   "Brownian motion: existence, uniqueness, and the strong Markov property. "
                   "The companion to the 1944 integral paper.",
        "abs_url": "https://doi.org/10.1090/memo/0004",
        "pdf_url": "https://doi.org/10.1090/memo/0004",
        "categories": ["math.PR"],
    },
    {
        "title": "On the Theory of the Brownian Motion",
        "authors": ["George E. Uhlenbeck", "Leonard S. Ornstein"],
        "year": 1930,
        "summary": "Derives the Ornstein-Uhlenbeck process — Brownian motion with a restoring "
                   "drift. The prototypical mean-reverting diffusion, used everywhere from "
                   "physics to interest rate modeling.",
        "abs_url": "https://doi.org/10.1103/PhysRev.36.823",
        "pdf_url": "https://doi.org/10.1103/PhysRev.36.823",
        "categories": ["math.PR"],
    },
    {
        "title": "Stochastic Problems in Physics and Astronomy",
        "authors": ["Subrahmanyan Chandrasekhar"],
        "year": 1943,
        "summary": "A comprehensive review of stochastic processes in physics: random walks, "
                   "Brownian motion, Fokker-Planck equations, and the first passage problem. "
                   "Widely cited as an accessible introduction to these ideas.",
        "abs_url": "https://doi.org/10.1103/RevModPhys.15.1",
        "pdf_url": "https://doi.org/10.1103/RevModPhys.15.1",
        "categories": ["math.PR"],
    },
    {
        "title": "On Transforming a Certain Class of Stochastic Processes by Absolutely Continuous Substitution of Measures",
        "authors": ["Igor V. Girsanov"],
        "year": 1960,
        "summary": "Proves the Girsanov theorem: under a change of probability measure, a "
                   "Brownian motion plus drift becomes a new Brownian motion. "
                   "The key tool for risk-neutral pricing in mathematical finance.",
        "abs_url": "https://doi.org/10.1137/1105027",
        "pdf_url": "https://doi.org/10.1137/1105027",
        "categories": ["math.PR"],
    },
    {
        "title": "Diffusion Processes in One Dimension",
        "authors": ["William Feller"],
        "year": 1954,
        "summary": "Classifies all one-dimensional diffusions via their boundary behavior — "
                   "the Feller boundary conditions. The definitive treatment of the "
                   "generators of one-dimensional Markov diffusion processes.",
        "abs_url": "https://doi.org/10.1090/S0002-9947-1954-0063607-6",
        "pdf_url": "https://doi.org/10.1090/S0002-9947-1954-0063607-6",
        "categories": ["math.PR"],
    },
    {
        "title": "Generalized Harmonic Analysis",
        "authors": ["Norbert Wiener"],
        "year": 1930,
        "summary": "Develops the spectral theory of stationary stochastic processes, "
                   "introducing the Wiener-Khinchin theorem relating autocorrelation to "
                   "power spectral density.",
        "abs_url": "https://doi.org/10.1007/BF02546511",
        "pdf_url": "https://doi.org/10.1007/BF02546511",
        "categories": ["math.PR"],
    },
    {
        "title": "A New Approach to Linear Filtering and Prediction Problems",
        "authors": ["Rudolf E. Kalman"],
        "year": 1960,
        "summary": "Introduces the Kalman filter — a recursive Bayesian estimator for linear "
                   "dynamical systems with Gaussian noise. Foundational to control theory, "
                   "signal processing, and modern state-space models.",
        "abs_url": "https://doi.org/10.1115/1.3662552",
        "pdf_url": "https://doi.org/10.1115/1.3662552",
        "categories": ["math.PR", "math.OC"],
    },
    {
        "title": "Limit Theorems for Stochastic Processes",
        "authors": ["Anatoliy V. Skorokhod"],
        "year": 1956,
        "summary": "Introduces the Skorokhod topology on the space D[0,1] of càdlàg functions "
                   "and proves weak convergence results for stochastic processes. "
                   "The standard framework for functional limit theorems.",
        "abs_url": "https://doi.org/10.1137/1101022",
        "pdf_url": "https://doi.org/10.1137/1101022",
        "categories": ["math.PR"],
    },
    # ── PDEs (select papers with direct probability connections) ──────────
    {
        "title": "Continuity of Solutions of Parabolic and Elliptic Equations",
        "authors": ["John F. Nash"],
        "year": 1958,
        "summary": "Proves Hölder continuity of solutions to second-order parabolic and elliptic "
                   "PDEs in divergence form — independently of De Giorgi. The Nash estimate "
                   "is central to regularity theory and has probabilistic interpretations.",
        "abs_url": "https://doi.org/10.2307/2372841",
        "pdf_url": "https://doi.org/10.2307/2372841",
        "categories": ["math.AP", "math.PR"],
    },
    {
        "title": "Hypoelliptic Second Order Differential Equations",
        "authors": ["Lars Hörmander"],
        "year": 1967,
        "summary": "Proves that a sum-of-squares operator is hypoelliptic (smoothing) under "
                   "the Hörmander bracket condition. Directly governs the regularity of "
                   "diffusion processes and their generators.",
        "abs_url": "https://doi.org/10.1007/BF02392081",
        "pdf_url": "https://doi.org/10.1007/BF02392081",
        "categories": ["math.AP", "math.PR"],
    },
    # ── Graph theory ──────────────────────────────────────────────────────
    {
        "title": "On Random Graphs I",
        "authors": ["Paul Erdős", "Alfréd Rényi"],
        "year": 1959,
        "summary": "Introduces the Erdős-Rényi random graph model G(n,M) and proves that "
                   "connectivity appears suddenly when M ≈ n log n / 2. The first paper in "
                   "the foundational pair on random graph theory.",
        "abs_url": "http://www.renyi.hu/~p_erdos/1959-11.pdf",
        "pdf_url": "http://www.renyi.hu/~p_erdos/1959-11.pdf",
        "categories": ["math.CO", "math.PR"],
    },
    {
        "title": "Algebraic Connectivity of Graphs",
        "authors": ["Miroslav Fiedler"],
        "year": 1973,
        "summary": "Introduces the second-smallest Laplacian eigenvalue — the Fiedler value — "
                   "as a measure of graph connectivity. The paper that launched algebraic "
                   "graph theory and spectral clustering.",
        "abs_url": "https://doi.org/10.21136/CMJ.1973.101168",
        "pdf_url": "https://doi.org/10.21136/CMJ.1973.101168",
        "categories": ["math.CO", "math.SP"],
    },
    {
        "title": "Eigenvalues and Expanders",
        "authors": ["Noga Alon"],
        "year": 1986,
        "summary": "Proves that a graph is an expander if and only if its second Laplacian "
                   "eigenvalue is bounded away from zero — the Cheeger inequality for graphs. "
                   "The central result linking spectral gaps to mixing times.",
        "abs_url": "https://doi.org/10.1007/BF02579166",
        "pdf_url": "https://doi.org/10.1007/BF02579166",
        "categories": ["math.CO", "math.PR"],
    },
    # ── Markov chains / MCMC ──────────────────────────────────────────────
    {
        "title": "Monte Carlo Sampling Methods Using Markov Chains and Their Applications",
        "authors": ["W. Keith Hastings"],
        "year": 1970,
        "summary": "Generalizes the Metropolis algorithm to asymmetric proposals: correct for "
                   "the asymmetry via the Hastings ratio. The Metropolis-Hastings algorithm "
                   "as used in Bayesian inference today.",
        "abs_url": "https://doi.org/10.1093/biomet/57.1.97",
        "pdf_url": "https://doi.org/10.1093/biomet/57.1.97",
        "categories": ["math.PR", "stat.CO"],
    },
    {
        "title": "Stochastic Relaxation, Gibbs Distributions, and the Bayesian Restoration of Images",
        "authors": ["Stuart Geman", "Donald Geman"],
        "year": 1984,
        "summary": "Introduces the Gibbs sampler — MCMC via coordinate-wise conditional "
                   "sampling — and applies it to image reconstruction. One of the most cited "
                   "papers in statistics and machine learning.",
        "abs_url": "https://doi.org/10.1109/TPAMI.1984.4767596",
        "pdf_url": "https://doi.org/10.1109/TPAMI.1984.4767596",
        "categories": ["math.PR", "stat.CO"],
    },
    {
        "title": "Markov Chains for Exploring Posterior Distributions",
        "authors": ["Luke Tierney"],
        "year": 1994,
        "summary": "Provides a rigorous theoretical treatment of MCMC: conditions for "
                   "ergodicity, convergence, and the validity of posterior averages. "
                   "The theoretical backbone of Bayesian MCMC practice.",
        "abs_url": "https://doi.org/10.1214/aos/1176325750",
        "pdf_url": "https://projecteuclid.org/euclid.aos/1176325750",
        "categories": ["math.PR", "stat.CO"],
    },
    # ── Financial mathematics ─────────────────────────────────────────────
    {
        "title": "Théorie de la spéculation",
        "authors": ["Louis Bachelier"],
        "year": 1900,
        "summary": "The first mathematical model of financial markets: models stock prices as "
                   "Brownian motion and prices options accordingly. Predates Einstein's "
                   "Brownian motion paper by five years.",
        "abs_url": "https://doi.org/10.24033/asens.476",
        "pdf_url": "https://www.numdam.org/item/ASENS_1900_3_17__21_0/",
        "categories": ["q-fin.MF", "math.PR"],
    },
    {
        "title": "Portfolio Selection",
        "authors": ["Harry Markowitz"],
        "year": 1952,
        "summary": "Introduces mean-variance portfolio optimization: the efficient frontier "
                   "and the idea that diversification reduces risk without sacrificing "
                   "expected return. Founded modern portfolio theory.",
        "abs_url": "https://doi.org/10.1111/j.1540-6261.1952.tb01525.x",
        "pdf_url": "https://doi.org/10.1111/j.1540-6261.1952.tb01525.x",
        "categories": ["q-fin.PM", "q-fin.MF"],
    },
    {
        "title": "The Pricing of Options and Corporate Liabilities",
        "authors": ["Fischer Black", "Myron Scholes"],
        "year": 1973,
        "summary": "Derives the Black-Scholes PDE and closed-form option pricing formula "
                   "using Itô calculus and no-arbitrage. The most celebrated result in "
                   "quantitative finance; changed markets and won the 1997 Nobel.",
        "abs_url": "https://doi.org/10.1086/260062",
        "pdf_url": "https://doi.org/10.1086/260062",
        "categories": ["q-fin.MF", "math.PR"],
    },
    {
        "title": "Theory of Rational Option Pricing",
        "authors": ["Robert C. Merton"],
        "year": 1973,
        "summary": "Extends Black-Scholes to dividends, American options, and continuous "
                   "hedging arguments. Proves put-call parity and derives closed-form "
                   "solutions for several exotic structures.",
        "abs_url": "https://doi.org/10.2307/3003143",
        "pdf_url": "https://doi.org/10.2307/3003143",
        "categories": ["q-fin.MF", "math.PR"],
    },
    {
        "title": "Martingales and Arbitrage in Multiperiod Securities Markets",
        "authors": ["J. Michael Harrison", "David M. Kreps"],
        "year": 1979,
        "summary": "Proves the first fundamental theorem of asset pricing: no-arbitrage is "
                   "equivalent to the existence of a risk-neutral (martingale) measure. "
                   "The theoretical foundation for derivative pricing.",
        "abs_url": "https://doi.org/10.1016/0022-0531(79)90043-7",
        "pdf_url": "https://doi.org/10.1016/0022-0531(79)90043-7",
        "categories": ["q-fin.MF", "math.PR"],
    },
    {
        "title": "Martingales and Stochastic Integrals in the Theory of Continuous Trading",
        "authors": ["J. Michael Harrison", "Stanley R. Pliska"],
        "year": 1981,
        "summary": "Extends the Harrison-Kreps theory to continuous time using Itô integrals "
                   "and proves market completeness is equivalent to uniqueness of the "
                   "martingale measure.",
        "abs_url": "https://doi.org/10.1016/0304-4149(81)90026-0",
        "pdf_url": "https://doi.org/10.1016/0304-4149(81)90026-0",
        "categories": ["q-fin.MF", "math.PR"],
    },
    {
        "title": "An Equilibrium Characterization of the Term Structure",
        "authors": ["Oldřich Vasicek"],
        "year": 1977,
        "summary": "Proposes the Vasicek model — an OU process for the short rate — and "
                   "derives closed-form bond prices. The first analytically tractable "
                   "equilibrium term-structure model.",
        "abs_url": "https://doi.org/10.1016/0304-405X(77)90016-2",
        "pdf_url": "https://doi.org/10.1016/0304-405X(77)90016-2",
        "categories": ["q-fin.MF", "math.PR"],
    },
    {
        "title": "A Theory of the Term Structure of Interest Rates",
        "authors": ["John C. Cox", "Jonathan E. Ingersoll", "Stephen A. Ross"],
        "year": 1985,
        "summary": "The CIR model: a square-root diffusion for the short rate that keeps "
                   "rates positive. Derives closed-form bond and option prices and remains "
                   "the benchmark for affine term-structure models.",
        "abs_url": "https://doi.org/10.2307/1911242",
        "pdf_url": "https://doi.org/10.2307/1911242",
        "categories": ["q-fin.MF", "math.PR"],
    },
    {
        "title": "A Closed-Form Solution for Options with Stochastic Volatility",
        "authors": ["Steven L. Heston"],
        "year": 1993,
        "summary": "Prices European options when volatility follows a CIR process correlated "
                   "with the asset. Gives a semi-closed-form characteristic-function solution "
                   "that captures the volatility smile.",
        "abs_url": "https://doi.org/10.1093/rfs/6.2.327",
        "pdf_url": "https://doi.org/10.1093/rfs/6.2.327",
        "categories": ["q-fin.MF", "math.PR"],
    },
    # ── Machine learning / neural networks ───────────────────────────────
    {
        "title": "Neural Networks and Physical Systems with Emergent Collective Computational Abilities",
        "authors": ["John J. Hopfield"],
        "year": 1982,
        "summary": "Introduces the Hopfield network as an energy-based associative memory. "
                   "Connects neural computation to statistical physics; a conceptual "
                   "ancestor of modern Boltzmann machines and diffusion models.",
        "abs_url": "https://doi.org/10.1073/pnas.79.8.2554",
        "pdf_url": "https://www.pnas.org/doi/pdf/10.1073/pnas.79.8.2554",
        "categories": ["cs.LG", "math.PR"],
    },
    {
        "title": "Learning Representations by Back-Propagating Errors",
        "authors": ["David E. Rumelhart", "Geoffrey E. Hinton", "Ronald J. Williams"],
        "year": 1986,
        "summary": "Popularizes the backpropagation algorithm for training multi-layer neural "
                   "networks. The gradient computation underlying all modern deep learning "
                   "optimization.",
        "abs_url": "https://doi.org/10.1038/323533a0",
        "pdf_url": "https://doi.org/10.1038/323533a0",
        "categories": ["cs.LG"],
    },
    {
        "title": "Maximum Likelihood from Incomplete Data via the EM Algorithm",
        "authors": ["Arthur P. Dempster", "Nan M. Laird", "Donald B. Rubin"],
        "year": 1977,
        "summary": "Introduces the EM algorithm: alternating expectation and maximization "
                   "steps to find MLEs with latent variables. Ubiquitous in mixture models, "
                   "HMMs, and probabilistic graphical models.",
        "abs_url": "https://doi.org/10.1111/j.2517-6161.1977.tb01600.x",
        "pdf_url": "https://doi.org/10.1111/j.2517-6161.1977.tb01600.x",
        "categories": ["math.ST", "cs.LG"],
    },
    {
        "title": "Support-Vector Networks",
        "authors": ["Corinna Cortes", "Vladimir Vapnik"],
        "year": 1995,
        "summary": "Introduces the support vector machine: a max-margin classifier with the "
                   "kernel trick for nonlinear boundaries. Dominated supervised learning "
                   "before the deep learning era.",
        "abs_url": "https://doi.org/10.1007/BF00994018",
        "pdf_url": "https://doi.org/10.1007/BF00994018",
        "categories": ["cs.LG", "math.ST"],
    },
    {
        "title": "A Decision-Theoretic Generalization of On-Line Learning and an Application to Boosting",
        "authors": ["Yoav Freund", "Robert E. Schapire"],
        "year": 1997,
        "summary": "Introduces AdaBoost — the first practical boosting algorithm. Combines "
                   "weak learners into a strong classifier and proved the first PAC-style "
                   "margin bound for boosting.",
        "abs_url": "https://doi.org/10.1006/jcss.1997.1504",
        "pdf_url": "https://doi.org/10.1006/jcss.1997.1504",
        "categories": ["cs.LG", "math.ST"],
    },
    {
        "title": "Gradient-Based Learning Applied to Document Recognition",
        "authors": ["Yann LeCun", "Léon Bottou", "Yoshua Bengio", "Patrick Haffner"],
        "year": 1998,
        "summary": "Introduces LeNet — a convolutional neural network trained end-to-end by "
                   "backprop for digit recognition. Established the template for modern "
                   "deep learning architectures.",
        "abs_url": "https://doi.org/10.1109/5.726791",
        "pdf_url": "http://yann.lecun.com/exdb/publis/pdf/lecun-01a.pdf",
        "categories": ["cs.LG", "cs.CV"],
    },
    # ── Information theory / foundations ─────────────────────────────────
    {
        "title": "A Mathematical Theory of Communication",
        "authors": ["Claude E. Shannon"],
        "year": 1948,
        "summary": "Founds information theory: defines entropy, channel capacity, and proves "
                   "the noisy-channel coding theorem. Shannon entropy is the bridge between "
                   "probability and the fundamental limits of communication.",
        "abs_url": "https://doi.org/10.1002/j.1538-7305.1948.tb01338.x",
        "pdf_url": "https://people.math.harvard.edu/~ctm/home/text/others/shannon/entropy/entropy.pdf",
        "categories": ["cs.IT", "math.PR"],
    },
    {
        "title": "On Computable Numbers, with an Application to the Entscheidungsproblem",
        "authors": ["Alan M. Turing"],
        "year": 1936,
        "summary": "Defines the Turing machine and proves there are uncomputable functions — "
                   "resolving Hilbert's decision problem negatively. The foundational paper "
                   "of theoretical computer science.",
        "abs_url": "https://doi.org/10.1112/plms/s2-42.1.230",
        "pdf_url": "https://doi.org/10.1112/plms/s2-42.1.230",
        "categories": ["cs.LO"],
    },
    # ── Random matrices ───────────────────────────────────────────────────
    {
        "title": "Characteristic Vectors of Bordered Matrices with Infinite Dimensions",
        "authors": ["Eugene P. Wigner"],
        "year": 1955,
        "summary": "Proves the Wigner semicircle law: the empirical spectral distribution "
                   "of a symmetric random matrix with i.i.d. entries converges to the "
                   "semicircle distribution. The founding result of random matrix theory.",
        "abs_url": "https://doi.org/10.2307/1970079",
        "pdf_url": "https://doi.org/10.2307/1970079",
        "categories": ["math.PR"],
    },
    # ── Game theory / equilibrium ─────────────────────────────────────────
    {
        "title": "Equilibrium Points in n-Person Games",
        "authors": ["John F. Nash"],
        "year": 1950,
        "summary": "Proves that every finite game has a mixed-strategy Nash equilibrium. "
                   "A fixed-point argument that transformed economics, evolutionary biology, "
                   "and the study of strategic interaction.",
        "abs_url": "https://doi.org/10.1073/pnas.36.1.48",
        "pdf_url": "https://www.pnas.org/doi/pdf/10.1073/pnas.36.1.48",
        "categories": ["math.PR", "econ.GN"],
    },
]

PAGE_SIZE = 100
MAX_PAGES = 5
REQUEST_PAUSE = 3.0
USER_AGENT = "weekly-arxiv-digest/1.0 (personal research digest)"

ANTHROPIC_MODEL = "claude-sonnet-4-6"

ARXIV_API = "http://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


# ──────────────────────────────────────────────────────────────────────────
# Fetch + parse
# ──────────────────────────────────────────────────────────────────────────

def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch_category(cat, cutoff):
    """Page through a category newest-first until we pass the cutoff date."""
    papers = []
    for page in range(MAX_PAGES):
        params = urllib.parse.urlencode({
            "search_query": f"cat:{cat}",
            "start": page * PAGE_SIZE,
            "max_results": PAGE_SIZE,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        })
        url = f"{ARXIV_API}?{params}"
        try:
            raw = _get(url)
        except Exception as e:                       # noqa: BLE001
            print(f"  ! fetch failed for {cat} page {page}: {e}", file=sys.stderr)
            time.sleep(REQUEST_PAUSE)
            continue

        batch = parse_atom(raw, cat)
        if not batch:
            break
        papers.extend(batch)
        oldest = min(p["published"] for p in batch)
        time.sleep(REQUEST_PAUSE)
        if oldest < cutoff:
            break

    return [p for p in papers if p["published"] >= cutoff]


def _make_classic_from_dict(d):
    """Build a paper dict from a hand-specified non-arXiv classic."""
    year = d.get("year", 2000)
    slug = re.sub(r"[^a-z0-9]", "_", d["title"].lower())[:40]
    return {
        "id": d.get("id", slug),
        "title": d["title"],
        "summary": d.get("summary", ""),
        "authors": d.get("authors", []),
        "published": dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc),
        "categories": d.get("categories", ["math.PR"]),
        "queried_cat": "classic",
        "abs_url": d["abs_url"],
        "pdf_url": d.get("pdf_url", d["abs_url"]),
        "score": 0.0,
        "is_classic": True,
    }


def _fetch_by_id(arxiv_id):
    """Fetch a single paper by arXiv ID. Returns a paper dict or None."""
    params = urllib.parse.urlencode({"id_list": arxiv_id, "max_results": 1})
    url = f"{ARXIV_API}?{params}"
    try:
        raw = _get(url)
        batch = parse_atom(raw, "classic")
        time.sleep(REQUEST_PAUSE)
        if batch:
            p = batch[0]
            p["score"] = score_paper(p)
            p["is_classic"] = True
            return p
    except Exception as e:                           # noqa: BLE001
        print(f"  ! fetch_by_id failed for {arxiv_id}: {e}", file=sys.stderr)
    return None


def parse_atom(raw, queried_cat):
    root = ET.fromstring(raw)
    out = []
    for entry in root.findall(f"{ATOM}entry"):
        id_url = entry.findtext(f"{ATOM}id", default="").strip()
        m = re.search(r"abs/([^v]+)(v\d+)?$", id_url)
        arxiv_id = m.group(1) if m else id_url

        title = _collapse(entry.findtext(f"{ATOM}title", default=""))
        summary = _collapse(entry.findtext(f"{ATOM}summary", default=""))

        published_str = entry.findtext(f"{ATOM}published", default="")
        try:
            published = dt.datetime.strptime(
                published_str, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue

        authors = [
            a.findtext(f"{ATOM}name", default="").strip()
            for a in entry.findall(f"{ATOM}author")
        ]
        cats = [
            c.get("term")
            for c in entry.findall(f"{ATOM}category")
            if c.get("term")
        ]

        abs_url = id_url
        pdf_url = id_url.replace("/abs/", "/pdf/")

        out.append({
            "id": arxiv_id,
            "title": title,
            "summary": summary,
            "authors": authors,
            "published": published,
            "categories": cats,
            "queried_cat": queried_cat,
            "abs_url": abs_url,
            "pdf_url": pdf_url,
        })
    return out


def _collapse(text):
    return re.sub(r"\s+", " ", text or "").strip()


# ──────────────────────────────────────────────────────────────────────────
# Seen-papers cache
# ──────────────────────────────────────────────────────────────────────────

def load_seen():
    """Return (set of seen IDs within retention window, list of recent titles)."""
    try:
        with open(SEEN_CACHE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return set(), []

    cutoff = dt.date.today() - dt.timedelta(weeks=SEEN_MAX_WEEKS)
    recent_cutoff = dt.date.today() - dt.timedelta(weeks=SEEN_RECENT_WEEKS)
    seen_ids = set()
    recent_titles = []
    for e in data.get("seen", []):
        try:
            d = dt.date.fromisoformat(e["date"])
        except (KeyError, ValueError):
            continue
        if d >= cutoff:
            seen_ids.add(e["id"])
        if d >= recent_cutoff and e.get("title"):
            recent_titles.append(e["title"])
    return seen_ids, recent_titles


def save_seen(papers):
    """Append newly selected paper IDs and titles to the cache file."""
    try:
        with open(SEEN_CACHE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"seen": []}

    today = dt.date.today().isoformat()
    existing_ids = {e["id"] for e in data["seen"]}
    for p in papers:
        if p["id"] not in existing_ids:
            data["seen"].append({
                "id": p["id"],
                "date": today,
                "title": p.get("title", ""),
            })

    cutoff = dt.date.today() - dt.timedelta(weeks=SEEN_MAX_WEEKS)
    data["seen"] = [
        e for e in data["seen"]
        if dt.date.fromisoformat(e.get("date", "1900-01-01")) >= cutoff
    ]

    with open(SEEN_CACHE, "w") as f:
        json.dump(data, f, indent=2)


# ──────────────────────────────────────────────────────────────────────────
# Score + candidate pool
# ──────────────────────────────────────────────────────────────────────────

def category_weight(paper):
    return max((CATEGORIES.get(c, 0.0) for c in paper["categories"]), default=0.0)


def score_paper(paper):
    title = paper["title"].lower()
    summary = paper["summary"].lower()

    score = CATEGORY_BIAS * category_weight(paper)

    for kw, w in KEYWORDS.items():
        if kw in title:
            score += TITLE_WEIGHT * w
        elif kw in summary:
            score += ABSTRACT_WEIGHT * w

    names = " ".join(paper["authors"]).lower()
    if any(a in names for a in AUTHORS):
        score += AUTHOR_BONUS

    return score


def is_probability(paper):
    return any(c in PROB_CATEGORIES for c in paper["categories"])


def build_candidate_pool(papers, seen_ids):
    """Score, dedup, exclude already-seen papers, return sorted candidate pools."""
    best = {}
    for p in papers:
        p["score"] = score_paper(p)
        if p["id"] not in best or p["score"] > best[p["id"]]["score"]:
            best[p["id"]] = p

    unique = [p for p in best.values()
              if p["score"] > MIN_SCORE and p["id"] not in seen_ids]

    prob = sorted(
        (p for p in unique if is_probability(p)),
        key=lambda p: p["score"], reverse=True,
    )
    other = sorted(
        (p for p in unique if not is_probability(p)),
        key=lambda p: p["score"], reverse=True,
    )
    return prob[:CANDIDATE_PROB], other[:CANDIDATE_OTHER]


def select_by_score(prob_pool, other_pool):
    """Fallback: pick top papers by score when Claude is unavailable."""
    chosen = prob_pool[:N_PROB] + other_pool[:N_OTHER]
    chosen.sort(key=lambda p: p["score"], reverse=True)
    return chosen


def select_classics():
    """Pick N_CLASSIC papers from CLASSIC_PAPERS, rotating by ISO week.

    Each entry is either an arXiv ID string (fetched live) or a dict with
    pre-filled metadata for non-arXiv papers.
    """
    if not CLASSIC_PAPERS:
        return []
    week = dt.date.today().isocalendar()[1]
    rng = random.Random(week)
    entries = rng.sample(CLASSIC_PAPERS, min(N_CLASSIC, len(CLASSIC_PAPERS)))
    papers = []
    for entry in entries:
        if isinstance(entry, str):
            print(f"  fetching classic {entry}…")
            p = _fetch_by_id(entry)
        else:
            p = _make_classic_from_dict(entry)
        if p:
            papers.append(p)
    return papers


# ──────────────────────────────────────────────────────────────────────────
# Claude selection + enrichment
# ──────────────────────────────────────────────────────────────────────────

def claude_select_and_enrich(prob_pool, other_pool, classic_papers, recent_titles):
    """
    Ask Claude to select the best papers from the candidate pools and write
    one-line notes for all selected + classic papers.
    Falls back to score-based selection if ANTHROPIC_API_KEY is not set.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return select_by_score(prob_pool, other_pool)

    all_new = prob_pool + other_pool
    if not all_new:
        return []

    n_prob = len(prob_pool)

    new_listing = "\n\n".join(
        f"[{i}] {p['title']}\n{p['summary'][:400]}"
        for i, p in enumerate(all_new)
    )

    classic_listing = ""
    if classic_papers:
        classic_listing = "\n\nCLASSIC PAPERS TO ANNOTATE:\n\n" + "\n\n".join(
            f"[C{i}] {p['title']}\n{p['summary'][:300]}"
            for i, p in enumerate(classic_papers)
        )

    recent_ctx = ""
    if recent_titles:
        recent_ctx = (
            "\n\nPAPERS THE USER RECEIVED IN THE LAST FEW WEEKS "
            "(avoid exact topic repeats):\n"
            + "\n".join(f"- {t}" for t in recent_titles[:20])
        )

    prompt = (
        "You are curating a weekly research digest for a final-year mathematics student.\n\n"
        "INTEREST PROFILE: probability theory (martingales, SDEs, Brownian motion, "
        "Stein's method, concentration inequalities, extreme value theory, large deviations), "
        "spectral graph theory (graph energy, Laplacian eigenvalues), "
        "diffusion/score-based generative models (score matching, flow matching), "
        "quantitative finance (pairs trading, statistical arbitrage, cointegration, "
        "regime-switching, market microstructure).\n"
        f"{recent_ctx}\n\n"
        f"CANDIDATE NEW PAPERS — indices 0–{n_prob - 1} are probability categories "
        f"(select exactly {N_PROB}), indices {n_prob}–{len(all_new) - 1} are adjacent "
        f"(select exactly {N_OTHER}). Choose based on fit with the interest profile, "
        f"not just recency:\n\n{new_listing}"
        f"{classic_listing}\n\n"
        "Return ONLY this JSON, no other text:\n"
        '{"prob": [<exactly ' + str(N_PROB) + ' indices from 0–' + str(n_prob - 1) + '>], '
        '"other": [<exactly ' + str(N_OTHER) + ' indices from ' + str(n_prob) + '–' + str(len(all_new) - 1) + '>], '
        '"notes_new": {"<index>": "<one sentence, max 20 words>", ...}, '
        '"notes_classic": ["<note for C0>", "<note for C1>", ...]}'
    )

    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())
        text = "".join(
            b.get("text", "") for b in data.get("content", [])
            if b.get("type") == "text"
        )
        text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE)
        result = json.loads(text.strip())

        prob_indices = result.get("prob", [])[:N_PROB]
        other_indices = result.get("other", [])[:N_OTHER]
        notes_new = result.get("notes_new", {})
        notes_classic = result.get("notes_classic", [])

        chosen = []
        for idx in prob_indices:
            if isinstance(idx, int) and 0 <= idx < n_prob:
                p = all_new[idx]
                p["note"] = notes_new.get(str(idx), "")
                chosen.append(p)
        for idx in other_indices:
            if isinstance(idx, int) and n_prob <= idx < len(all_new):
                p = all_new[idx]
                p["note"] = notes_new.get(str(idx), "")
                chosen.append(p)

        for i, p in enumerate(classic_papers):
            if i < len(notes_classic):
                p["note"] = notes_classic[i]

        # pad with score-based picks if Claude returned too few
        if len(chosen) < N_PROB + N_OTHER:
            used = {p["id"] for p in chosen}
            for p in select_by_score(prob_pool, other_pool):
                if p["id"] not in used:
                    chosen.append(p)
                    used.add(p["id"])
                if len(chosen) >= N_PROB + N_OTHER:
                    break

        return chosen

    except Exception as e:                           # noqa: BLE001
        print(f"  ! Claude selection skipped: {e}", file=sys.stderr)
        return select_by_score(prob_pool, other_pool)


# ──────────────────────────────────────────────────────────────────────────
# Render + send
# ──────────────────────────────────────────────────────────────────────────

def _paper_text_block(i, p):
    tag = "PR" if is_probability(p) else p["queried_cat"]
    lines = [f"{i}. [{tag}] {p['title']}"]
    lines.append(f"   {', '.join(p['authors'][:4])}"
                 + (" et al." if len(p["authors"]) > 4 else ""))
    if p.get("note"):
        lines.append(f"   → {p['note']}")
    lines.append(f"   {p['abs_url']}")
    lines.append("")
    return lines


def render_text(new_papers, classic_papers, cutoff):
    lines = [f"Weekly arXiv digest — since {cutoff.date()}", ""]
    for i, p in enumerate(classic_papers, 1):
        lines.extend(_paper_text_block(i, p))
    if new_papers:
        lines.append("── Last week " + "─" * 47)
        lines.append("")
        for i, p in enumerate(new_papers, 1):
            lines.extend(_paper_text_block(i, p))
    return "\n".join(lines)


def _paper_html_row(i, p):
    tag = "math.PR" if is_probability(p) else p["queried_cat"]
    authors = html.escape(", ".join(p["authors"][:4]))
    if len(p["authors"]) > 4:
        authors += " et al."
    note = ""
    if p.get("note"):
        note = (f'<div style="color:#444;font-size:13px;margin-top:4px">'
                f'→ {html.escape(p["note"])}</div>')
    return f"""
        <div style="margin:0 0 22px 0;padding:0 0 18px 0;border-bottom:1px solid #eee">
          <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.05em">
            {i} · {html.escape(tag)}
          </div>
          <div style="font-size:16px;font-weight:600;margin:3px 0">
            <a href="{html.escape(p['abs_url'])}" style="color:#1a1a1a;text-decoration:none">
              {html.escape(p['title'])}</a>
          </div>
          <div style="font-size:13px;color:#666">{authors}</div>
          {note}
          <div style="font-size:12px;margin-top:6px">
            <a href="{html.escape(p['abs_url'])}" style="color:#3355cc">abstract</a>
            &nbsp;·&nbsp;
            <a href="{html.escape(p['pdf_url'])}" style="color:#3355cc">pdf</a>
          </div>
        </div>"""


def render_html(new_papers, classic_papers, cutoff):
    classic_rows = "".join(_paper_html_row(i, p) for i, p in enumerate(classic_papers, 1))

    new_section = ""
    if new_papers:
        new_rows = "".join(_paper_html_row(i, p) for i, p in enumerate(new_papers, 1))
        new_section = f"""
      <div style="font-size:15px;font-weight:700;margin:32px 0 16px;
                  padding-top:24px;border-top:2px solid #e0e0e0;color:#555">
        Last week
      </div>
      {new_rows}"""

    return f"""<!DOCTYPE html><html><body style="margin:0;background:#fafafa">
    <div style="max-width:680px;margin:0 auto;padding:28px 24px;
                font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
      <div style="font-size:20px;font-weight:700;margin-bottom:4px">
        Weekly arXiv digest</div>
      <div style="font-size:13px;color:#888;margin-bottom:24px">
        {len(classic_papers)} curated · {len(new_papers)} last week · since {cutoff.date()}</div>
      {classic_rows}
      {new_section}
      <div style="font-size:11px;color:#aaa;margin-top:18px">
        Generated by arxiv_digest.py · tune your profile in CONFIG</div>
    </div></body></html>"""


def send_email(subject, text_body, html_body):
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    to_addrs = [a.strip() for a in os.environ.get("MAIL_TO", user).split(",") if a.strip()]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(user, password)
        server.sendmail(user, to_addrs, msg.as_string())
    print(f"  ✓ emailed {len(html_body)} bytes to {', '.join(to_addrs)}")


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Weekly arXiv digest.")
    ap.add_argument("--days", type=int, default=7,
                    help="look-back window in days (default 7)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print to stdout instead of emailing")
    args = ap.parse_args()

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
    print(f"Fetching since {cutoff.date()} across {len(CATEGORIES)} categories…")

    all_papers = []
    for cat in CATEGORIES:
        got = fetch_category(cat, cutoff)
        print(f"  {cat}: {len(got)} in window")
        all_papers.extend(got)

    seen_ids, seen_titles = load_seen()
    print(f"Loaded {len(seen_ids)} seen IDs from cache.")

    prob_pool, other_pool = build_candidate_pool(all_papers, seen_ids)
    print(f"Candidate pool: {len(prob_pool)} prob, {len(other_pool)} other.")

    print(f"Fetching {N_CLASSIC} classic papers…")
    chosen_classics = select_classics()
    print(f"Fetched {len(chosen_classics)} classics.")

    chosen_new = claude_select_and_enrich(prob_pool, other_pool, chosen_classics, seen_titles)
    print(f"Selected {len(chosen_new)} new papers.")

    if not args.dry_run:
        save_seen(chosen_new + chosen_classics)
        print("Updated seen-papers cache.")

    subject = (f"arXiv digest — {len(chosen_classics)} curated + {len(chosen_new)} new"
               f" ({dt.date.today():%b %d})")
    text_body = render_text(chosen_new, chosen_classics, cutoff)
    html_body = render_html(chosen_new, chosen_classics, cutoff)

    if args.dry_run or not os.environ.get("SMTP_USER"):
        print("\n" + "=" * 60 + "\n")
        print(text_body)
        if not args.dry_run:
            print("(no SMTP_USER set — printed instead of emailing)",
                  file=sys.stderr)
    else:
        send_email(subject, text_body, html_body)


if __name__ == "__main__":
    main()
