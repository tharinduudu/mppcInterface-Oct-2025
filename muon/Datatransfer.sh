#Japan Data Transfer Script to GSU Phys2 Server

echo "Copying files to Phy2 server"
source_dir_muon_data="/home/cosmic/mppcinterface-oct-2022/firmware/libraries/slowControl"
source_dir_press_data="/home/cosmic/bmp280_logs"

dest_dir_muon_data="/home/dsk3/xiaochun/Cosmic/Turkey/Turkey_Obs/muonData"
dest_dir_press_data="/home/dsk3/xiaochun/Cosmic/Turkey/Turkey_Obs/prsData"

source_host="131.96.55.85"
check_string="log"

file_name_muon_data=$(ls $source_dir_muon_data -1t | head -n 1)
file_name_press_data=$(ls $source_dir_press_data -1t | head -n 1)

#echo $file_name2
scp -P 2998 -i /home/cosmic/.ssh/id_rsa ${source_dir_muon_data}/${file_name_muon_data} xiaochun@${source_host}:${dest_dir_muon_data}
scp -P 2998 -i /home/cosmic/.ssh/id_rsa ${source_dir_press_data}/${file_name_press_data}  xiaochun@${source_host}:${dest_dir_press_data}
