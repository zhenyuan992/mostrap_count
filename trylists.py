print 'ho'
#readfile
# index | filename | bool uploaded | int reboot
# 1 | hahah.jpg | 1 | 3
#list_of_lines[0] #this is the first line
datafile_path = 'C:\Users\User\Desktop\datafile.txt'
folder_in_pi= ' ' #for uploading to
folder_in_dropbox = ' ' #for uploading from
texts_from_file=[]
"""
textf=open(datafile_path,'w')
lines_of_text = ["4 | hahah.jpg | 1 | 3\n", "another line of text\n", "2 | hahah.jpg | 0 | 3\n"]
textf.write(lines_of_text)
textf.close()
"""
#defining functions ************************************************
def readfiletolines():
    textf=open(datafile_path,'r')
    global texts_from_file
    texts_from_file = textf.readlines()
    textf.close()

def textValueAdding(value): #value = -1 for rebootadding,-2 for upload
    global texts_from_file
    tobeedited=texts_from_file.pop()
    tobeedited=tobeedited.split('|')
    tobeedited[value]=" "+str(int(tobeedited[value])+1)+" "
    tobeedited = "|".join(tobeedited)
    texts_from_file.append(tobeedited)

def textAddLine(index,name,uploaded):
    global texts_from_file
    tobeadded="\n"+str(index)+" | "+name+" | "+str(uploaded)+" | 0 "
    texts_from_file.append(tobeadded)
    
def writetofile():
    global texts_from_file
    textf=open(datafile_path,'w')
    textf.writelines(texts_from_file)
#defined functions **********************************************

readfiletolines()
#textAddLine(7,"hahah.jpg",0)
#textValueAdding(-1)
writetofile()
stringg = ' '
for liness in texts_from_file:
    stringg+=liness
print stringg
iteminalist=texts_from_file[-1] #reads the last item
value_from_last_item=iteminalist.split('|')
print value_from_last_item[0] #index of the last item
print value_from_last_item[2] #bool of sucessful uploading
print value_from_last_item[3] #number of reboots(if less than 6, ++ and reboot)
                              #(if 6 or more, append and end)

index_last_item = int(value_from_last_item[0])
name_last_item = str(value_from_last_item[1])
last_item_uploaded = int(value_from_last_item[2])
times_of_reboot = int(value_from_last_item[3])

#set default vairables
hasWifi = False
newpicture = False
nameofpicture =' '
indexofpicture=0
uploaded = False
rebooting = True
#execute checkwifi
#execute logic
if last_item_uploaded:
    print 'last item uploaded, proceed with'
    #takepicture(with index_last_item++) = newpicture
    textAddLine(indexofpicture,nameofpicture,0)
elif times_of_reboot > 5:
    print 'rebooted too many times, take new picture'
    #take picture (with index_last_item++) = newpicture
    textAddLine(indexofpicture,nameofpicture,0)
elif not hasWifi:
    print ' system has to reboot'
    rebooting = True

if hasWifi:
    uploaded = True #mainuploadseq()
    rebooting = not uploaded # upload fail, then it will try to reboot

if (times_of_reboot > 5): # no matter what, if it reboots 5 times, its time to shutdown
    rebooting = False

#execute finally part
#finally part
finallyy = True
  
if finallyy:
    if uploaded or (not rebooting):
        #write to file
        if uploaded:
            textValueAdding(-2)
        writetofile()
        #shutdownseq
        print 'sys is shutting down'
    else:
        #times_of_reboot++, write to file
        textValueAdding(-1)
        writetofile()
        #rebootseq
        print 'rebooting seq for real'
