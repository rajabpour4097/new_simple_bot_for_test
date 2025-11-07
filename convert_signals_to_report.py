"""
تبدیل فایل سیگنال‌ها به فرمت ReportHistory برای بهینه‌ساز خروج
این اسکریپت سیگنال‌های ورود را به معاملات شبیه‌سازی شده تبدیل می‌کند
"""
import pandas as pd
import numpy as np
from datetime import timedelta

def convert_signals_to_report(
    signals_path: str = "EURUSD_signals_all_sorted_istanbul.csv",
    output_path: str = "ReportHistory.csv",
    default_duration_hours: int = 24
):
    """
    تبدیل سیگنال‌ها به فرمت ReportHistory
    
    Args:
        signals_path: مسیر فایل سیگنال‌ها
        output_path: مسیر خروجی
        default_duration_hours: مدت زمان پیش‌فرض هر معامله (ساعت)
    """
    print(f"📖 Reading signals from {signals_path}...")
    df = pd.read_csv(signals_path)
    
    print(f"✓ Loaded {len(df)} signals")
    print(f"  Date range: {df['Time'].min()} to {df['Time'].max()}")
    
    # تبدیل زمان به datetime
    df['Time'] = pd.to_datetime(df['Time'])
    
    # ساخت DataFrame با فرمت ReportHistory
    report = pd.DataFrame()
    
    # ستون‌های اصلی
    report['Time'] = df['Time'].dt.strftime('%Y.%m.%d %H:%M:%S')  # زمان باز شدن
    report['Position'] = range(90000000, 90000000 + len(df))  # شماره پوزیشن
    report['Symbol'] = df['symbol']
    report['Type'] = df['direction']  # buy/sell
    report['Volume'] = 0.01  # حجم پیش‌فرض
    report['Price'] = df['entry']  # قیمت ورود
    report['S / L'] = df['sl']  # Stop Loss
    report['T / P'] = df['tp']  # Take Profit (ممکن است NaN باشد)
    
    # زمان بسته شدن: زمان باز شدن + مدت پیش‌فرض
    close_times = df['Time'] + timedelta(hours=default_duration_hours)
    report['Time.1'] = close_times.dt.strftime('%Y.%m.%d %H:%M:%S')
    
    # قیمت بسته شدن: فرض می‌کنیم معاملات به SL رسیده‌اند (بدبینانه‌ترین حالت)
    # بهینه‌ساز روی تیک‌ها خروج واقعی را شبیه‌سازی می‌کند
    report['Price.1'] = df['sl']
    
    # ستون‌های مالی
    report['Commission'] = -0.20  # کمیسیون پیش‌فرض
    report['Swap'] = 0.0
    
    # سود/ضرر: فرض می‌کنیم به SL رسیده (-1R)
    # محاسبه ریسک برای هر معامله
    risk = (df['entry'] - df['sl']).abs()
    pip_value = 0.01 * 10000  # برای EURUSD با حجم 0.01
    report['Profit'] = -risk * pip_value
    
    # برچسب نتیجه
    report['result'] = 'loss'  # فرض پیش‌فرض (بهینه‌ساز خروج واقعی را محاسبه می‌کند)
    
    # ذخیره
    report.to_csv(output_path, index=False)
    print(f"\n✅ Converted report saved to: {output_path}")
    print(f"   Total trades: {len(report)}")
    print(f"   Format: MetaTrader ReportHistory compatible")
    
    # نمایش نمونه
    print("\n📊 Sample (first 3 rows):")
    print(report.head(3).to_string())
    
    # آمار
    print(f"\n📈 Statistics:")
    print(f"   Buy trades: {(report['Type'] == 'buy').sum()}")
    print(f"   Sell trades: {(report['Type'] == 'sell').sum()}")
    print(f"   Time range: {report['Time'].min()} → {report['Time.1'].max()}")
    
    # بررسی اینکه Time != Time.1
    sample_check = pd.read_csv(output_path)
    time_different = (sample_check['Time'] != sample_check.iloc[:, 8]).all()
    print(f"\n✓ Verification: Time ≠ Time.1 for all rows: {time_different}")
    
    return report


if __name__ == "__main__":
    print("=" * 60)
    print("🔄 Converting Signals to ReportHistory Format")
    print("=" * 60)
    
    # پشتیبان‌گیری از فایل قدیمی
    import os
    if os.path.exists("ReportHistory.csv"):
        import shutil
        shutil.copy("ReportHistory.csv", "ReportHistory_backup.csv")
        print("💾 Backup created: ReportHistory_backup.csv")
    
    # تبدیل
    convert_signals_to_report(
        signals_path="EURUSD_signals_all_sorted_istanbul.csv",
        output_path="ReportHistory.csv",
        default_duration_hours=48  # هر معامله 48 ساعت زمان دارد
    )
    
    print("\n" + "=" * 60)
    print("✅ Done! Now you can run: python grid_exit_optimization.py")
    print("=" * 60)
