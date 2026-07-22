'use client';

import styles from './Header.module.css';
import { Bell, Search, User } from 'lucide-react';

export default function Header() {
  return (
    <header className={styles.header}>
      <div className={styles.search}>
        <Search size={18} className={styles.searchIcon} />
        <input 
          type="text" 
          placeholder="Search agents, evaluations, or runs..." 
          className={styles.searchInput}
        />
      </div>
      
      <div className={styles.actions}>
        <button className={styles.iconButton}>
          <Bell size={20} />
          <span className={styles.badge}></span>
        </button>
        <div className={styles.avatar}>
          <User size={20} />
        </div>
      </div>
    </header>
  );
}
