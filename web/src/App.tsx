import { useState } from 'react';
import Bounties from './Bounties';
import Wallet from './Wallet';
import { Icon } from './icons';

type Tab = 'wallet' | 'bounties';

function initialTab(): Tab {
  const q = new URLSearchParams(window.location.search);
  return q.has('hunt') || q.has('run') || q.has('payment_error') ? 'bounties' : 'wallet';
}

export default function App() {
  const [tab, setTab] = useState<Tab>(initialTab);
  return (
    <div className={`page page-${tab}`}>
      <header className="gp-top">
        <div className="gp-brand">
          <span className="gp-logo"><Icon name="wallet" size={20} /></span>
          <div>
            <b>Guild Pay</b>
            <span>A PayPal wallet your AI can't waste</span>
          </div>
        </div>
        <nav className="gp-tabs" role="tablist" aria-label="Guild">
          <button role="tab" aria-selected={tab === 'wallet'} className={tab === 'wallet' ? 'on' : ''} onClick={() => setTab('wallet')}>
            Wallet
          </button>
          <button role="tab" aria-selected={tab === 'bounties'} className={tab === 'bounties' ? 'on' : ''} onClick={() => setTab('bounties')}>
            Deal bounties
          </button>
        </nav>
      </header>

      {tab === 'wallet' ? <Wallet /> : <Bounties />}

      <footer className="foot">
        Powered by PayPal (sandbox only). Demo shops and creators are labelled and served by Guild itself; ShopperBot is scripted unless live mode is on.{' '}
        <a href="/api/docs">API</a> · <a href="https://github.com/Bigreddroid/Guild/blob/main/docs/AGENTS.md">Agent guide</a> ·{' '}
        <a href="https://github.com/Bigreddroid/Guild">Source (MIT)</a>
      </footer>
    </div>
  );
}
