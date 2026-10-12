import { useState } from 'react';
import Bounties from './Bounties';
import Wallet from './Wallet';

type Tab = 'wallet' | 'bounties';

function initialTab(): Tab {
  const q = new URLSearchParams(window.location.search);
  return q.has('hunt') || q.has('run') || q.has('payment_error') ? 'bounties' : 'wallet';
}

export default function App() {
  const [tab, setTab] = useState<Tab>(initialTab);
  return (
    <div className="page">
      <header className="top">
        <div className="brand">
          <span className="logo" aria-hidden>G</span>
          <div>
            <h1>Guild Pay</h1>
            <p className="tag">A safe PayPal wallet any AI agent can use. Every purchase is checked before money moves.</p>
          </div>
        </div>
        <nav className="tabs" role="tablist" aria-label="Guild">
          <button role="tab" aria-selected={tab === 'wallet'} className={tab === 'wallet' ? 'on' : ''} onClick={() => setTab('wallet')}>
            Agent wallet
          </button>
          <button role="tab" aria-selected={tab === 'bounties'} className={tab === 'bounties' ? 'on' : ''} onClick={() => setTab('bounties')}>
            Deal bounties
          </button>
        </nav>
      </header>

      {tab === 'wallet' ? <Wallet /> : <Bounties />}

      <footer className="foot">
        PayPal sandbox only. Demo shops are labelled and served by Guild itself; ShopperBot is scripted unless live mode is on.{' '}
        <a href="/api/docs">API</a> · <a href="https://github.com/Bigreddroid/Guild/blob/main/docs/AGENTS.md">Agent guide</a> ·{' '}
        <a href="https://github.com/Bigreddroid/Guild">Source (MIT)</a>
      </footer>
    </div>
  );
}
