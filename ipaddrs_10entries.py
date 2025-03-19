import yaml
import ipaddress
import random

# Define unique values for each category
LOBs = [f"LOB_{i+1}" for i in range(20)]
Owners = [f"Owner_{i+1}" for i in range(1000)]
AppNames = [f"App_{i+1}" for i in range(2000)]
L3s = [f"L3_{i+1}" for i in range(1000)]
L3_IT_Orgs = [f"L3IT_{i+1}" for i in range(500)]
Business_Criticality = list(range(1, 11))  # 1 through 10
Crown_Jewel = ["No"] * 90 + ["Yes"] * 10  # 90% No, 10% Yes

def generate_ipaddrs_yaml(filename="ipaddrs.yml", total_entries=50000, ips_per_appcode=25, ips_per_domain=100):
    base_network = ipaddress.IPv4Network("10.0.0.0/14")  # Expanded subnet
    ip_list = list(base_network.hosts())[:total_entries]  # Get exactly 50,000 IPs

    ip_data = {}
    appcode_counter = 1  # Track unique appcodes

    for i, ip in enumerate(ip_list):
        ip_address = f"{ip}/32"

        # Assign a unique appcode for every 25 IPs
        if i % ips_per_appcode == 0:
            appcode = f"APP{appcode_counter:06d}"
            appcode_counter += 1

        # Assign a unique device name per IP
        device_name = f"some_server_{i+1}"

        # Assign a unique device domain every 100 IPs
        domain_index = (i // ips_per_domain) + 1
        device_domain = f"some_domain_{domain_index}.elf.com"

        # Assign metadata fields with randomized selections
        metadata = {
            ".appcode": appcode,
            ".device.domain": device_domain,
            ".device.name": device_name,
            ".line_of_business": random.choice(LOBs),
            ".owner": random.choice(Owners),
            ".app_name": random.choice(AppNames),
            ".L3": random.choice(L3s),
            ".L3_IT_Organization": random.choice(L3_IT_Orgs),
            ".business_criticality": random.choice(Business_Criticality),
            ".crown_jewel": random.choice(Crown_Jewel)
        }

        ip_data[ip_address] = {"metadata": metadata}

        # Debugging progress
        if (i + 1) % (total_entries // 10) == 0:
            print(f"Processed {i+1} IPs, last appcode assigned: {appcode}")

    print(f"Final appcode assigned: {appcode}")

    with open(filename, "w") as file:
        yaml.dump(ip_data, file, default_flow_style=False)
        print(f"✅ Successfully wrote {filename} with {total_entries} entries.")

if __name__ == "__main__":
    generate_ipaddrs_yaml()
