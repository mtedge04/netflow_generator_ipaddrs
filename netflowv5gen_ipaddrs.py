import random
import socket
import struct
import time
import json
import yaml
import ipaddress
import multiprocessing
import itertools
import signal
import sys
import queue

def load_config(config_file):
    """Loads configuration from a JSON file."""
    with open(config_file, "r") as f:
        return json.load(f)

def load_enrichment_ips(filename="ipaddrs.yml"):
    """Loads source IPs from a YAML file, strips /32, and ensures valid IPv4 addresses."""
    with open(filename, "r") as file:
        data = yaml.safe_load(file)

    ip_list = [ip.split('/')[0] for ip in data.keys() if is_valid_ipv4(ip.split('/')[0])]

    if not ip_list:
        raise ValueError("No valid IPv4 addresses found in ipaddrs.yml after processing.")

    return itertools.cycle(ip_list)

def is_valid_ipv4(ip):
    """Returns True if the given string is a valid IPv4 address."""
    try:
        ipaddress.IPv4Address(ip)
        return True
    except ipaddress.AddressValueError:
        return False

def generate_random_ip_from_subnet(subnet):
    """Generates a random IP address from a given subnet."""
    network = ipaddress.IPv4Network(subnet, strict=False)
    return str(random.choice(list(network.hosts())))  

def generate_netflow_v5_packet(src_ip, dst_ip, flow_sequence, enrichment_ips, config):
    """Generates a valid NetFlow v5 packet with multiple records."""
    flow_count = 10  
    netflow_header = struct.pack(
        "!HHIIIIBBH",
        5,  
        flow_count,  
        int(time.time() * 1000) & 0xFFFFFFFF,  
        int(time.time()),  
        int((time.time() % 1) * 1e9) & 0xFFFFFFFF,  
        flow_sequence,
        0,  
        0,  
        0   
    )

    flow_records = b"".join(generate_netflow_v5_record(enrichment_ips, config) for _ in range(flow_count))

    udp_payload = netflow_header + flow_records  
    udp_header = struct.pack("!HHHH", 2055, 2055, 8 + len(udp_payload), 0)  

    ip_header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45, 0, 20 + len(udp_header) + len(udp_payload), 0,
        0, 64, socket.IPPROTO_UDP, 0,
        socket.inet_aton(src_ip), socket.inet_aton(dst_ip)
    )

    return ip_header + udp_header + udp_payload

def generate_netflow_v5_record(enrichment_ips, config):
    """Generates a single NetFlow v5 flow record (48 bytes) using enrichment IPs."""
    src_ip = next(enrichment_ips)  

    if not is_valid_ipv4(src_ip):  
        print(f"[ERROR] Invalid IP in enrichment data: {src_ip}")  
        return b""  

    dst_ip = generate_random_ip_from_subnet(config["destination_ip_subnet"])

    src_ip_bytes = socket.inet_aton(src_ip)
    dst_ip_bytes = socket.inet_aton(dst_ip)
    next_hop = socket.inet_aton("0.0.0.0")

    return struct.pack(
        "!4s4s4sHHIIIIHHxBBBHHBBxx",
        src_ip_bytes, dst_ip_bytes, next_hop,  
        random.randint(17000, 17099),  
        random.randint(17000, 17099),  
        random.randint(1, 1000),  
        random.randint(1, 100000),  
        int(time.time() * 1000) & 0xFFFFFFFF,  
        (int(time.time() * 1000) + random.randint(1, 1000)) & 0xFFFFFFFF,  
        random.randint(1024, 65535),  
        random.randint(1024, 65535),  
        random.randint(0, 255),  
        random.choice([6, 17]),  
        random.randint(0, 255),  
        random.randint(0, 65535),  
        random.randint(0, 65535),  
        random.randint(0, 32),  
        random.randint(0, 32)
    )

def rate_limited_sender(config, rate_queue, stop_event):
    """Rate-limited packet sender."""
    collector_ip = config["collector_ip"]
    collector_port = config["collector_port"]
    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)

    try:
        while not stop_event.is_set():
            try:
                # Get packet with a timeout to check stop event
                packet = rate_queue.get(timeout=0.1)
                sock.sendto(packet['data'], (collector_ip, collector_port))
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Error sending packet: {e}")

    except KeyboardInterrupt:
        print("\nStopping sender...")
    finally:
        sock.close()

def rate_controller(config, target_fps, ip_list, enrichment_ips, rate_queue, stop_event):
    """Controls packet generation rate using a token bucket algorithm."""
    flow_sequence = random.randint(0, 2**32 - 1)
    
    # Token bucket parameters
    max_tokens = target_fps  # Maximum tokens (burst capacity)
    tokens = max_tokens
    last_refill_time = time.time()
    refill_interval = 1.0  # Refill every second

    try:
        while not stop_event.is_set():
            current_time = time.time()

            # Refill tokens (replenish FPS tokens every second)
            time_since_last_refill = current_time - last_refill_time
            if time_since_last_refill >= refill_interval:
                tokens = min(max_tokens, tokens + int(time_since_last_refill * target_fps))
                last_refill_time = current_time

            # Generate and send packets only if tokens are available
            if tokens > 0:
                src_ip = str(random.choice(ip_list))
                
                try:
                    packet = generate_netflow_v5_packet(src_ip, config['collector_ip'], flow_sequence, enrichment_ips, config)
                    flow_sequence += 1
                    
                    # Put packet in queue with blocking to prevent overwhelming
                    rate_queue.put({'data': packet}, block=True, timeout=0.1)
                    tokens -= 1
                except queue.Full:
                    # Queue is full, skip this packet
                    time.sleep(0.001)
                except Exception as e:
                    print(f"Error generating packet: {e}")
            else:
                # No tokens, sleep briefly to prevent tight loop
                time.sleep(0.001)

    except KeyboardInterrupt:
        print("\nStopping rate controller...")
    finally:
        stop_event.set()

def main():
    config = load_config("config.json")
    flows_per_second = config["flows_per_second"]
    number_of_exporters = config.get("number_of_exporters", 10000)
    source_packet_subnet = config["source_packet_subnet"]

    # Network and IP setup
    base_network = ipaddress.IPv4Network(source_packet_subnet)
    ip_list = list(base_network.hosts())[:number_of_exporters]
    enrichment_ips = load_enrichment_ips()

    # Shared queue and stop event for inter-process communication
    rate_queue = multiprocessing.Queue(maxsize=flows_per_second)
    stop_event = multiprocessing.Event()

    print(f"Generating NetFlow at {flows_per_second} flows per second")

    # Create sender and rate controller processes
    sender = multiprocessing.Process(
        target=rate_limited_sender, 
        args=(config, rate_queue, stop_event)
    )
    rate_controller_proc = multiprocessing.Process(
        target=rate_controller, 
        args=(config, flows_per_second, ip_list, enrichment_ips, rate_queue, stop_event)
    )

    try:
        sender.start()
        rate_controller_proc.start()

        # Wait for processes to complete or be interrupted
        sender.join()
        rate_controller_proc.join()

    except KeyboardInterrupt:
        print("\nInterrupted by user. Stopping...")
        stop_event.set()
    finally:
        # Ensure clean termination
        stop_event.set()
        sender.terminate()
        rate_controller_proc.terminate()
        sender.join()
        rate_controller_proc.join()

if __name__ == "__main__":
    main()
