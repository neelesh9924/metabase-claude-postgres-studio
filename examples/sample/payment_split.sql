select mode as "Payment mode", tickets as "Tickets"
from (values ('Cash', 5400), ('UPI', 3100), ('Card', 900), ('Pass', 350), ('Wallet', 120), ('Other online', 90)) as t(mode, tickets)
order by 2 desc
