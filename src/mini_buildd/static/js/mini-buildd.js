// Make selection on a (multiple) select via a regex
// selectID: id of the 'select' element
// regexID : id of the input element with the regex in 'value'
function mbdSelectByRegex(selectID, regexID)
{
		var select = document.getElementById(selectID);
		var regex = document.getElementById(regexID).value;
		for (i=0; i < select.length; i++)
		{
				select[i].selected = select[i].text.match(regex);
		}
};
